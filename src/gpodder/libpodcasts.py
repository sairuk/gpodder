
#
# gPodder (a media aggregator / podcast client)
# Copyright (C) 2005-2007 Thomas Perl <thp at perli.net>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, 
# MA  02110-1301, USA.
#


#
#  libpodcasts.py -- data classes for gpodder
#  thomas perl <thp@perli.net>   20051029
#
#

import gtk
import gobject
import pango

from gpodder import util

from liblogger import log
import libgpodder

from os.path import exists
from os.path import basename
from os.path import splitext
import os.path
import os
import glob
import shutil
import sys
from urllib import unquote

from types import ListType
from datetime import datetime
from time import time

from liblocdbwriter import writeLocalDB
from liblocdbreader import readLocalDB

from libtagupdate import update_metadata_on_file
from libtagupdate import tagging_supported

from threading import Event
from libwget import downloadThread
import re

from email.Utils import mktime_tz
from email.Utils import parsedate_tz

from xml.sax import saxutils

import md5

import string

class podcastChannel(ListType):
    """holds data for a complete channel"""

    def __init__( self, url = "", title = "", link = "", description = ""):
        self.url = url
        self.title = title
        self.link = link
        self.description = util.remove_html_tags( description)
        self.image = None
        self.pubDate = datetime.now().ctime()
        self.language = ''
        self.copyright = ''
        self.webMaster = ''
        self.downloaded = None
        # should this channel be synced to devices? (ex: iPod)
        self.sync_to_devices = True
        # if this is set to true, device syncing (ex: iPod) should treat this as music, not as podcast)
        self.is_music_channel = False
        # to which playlist should be synced when "is_music_channel" is true?
        self.device_playlist_name = 'gPodder'
        # if set, this overrides the channel-provided title
        self.override_title = ''
        self.username = ''
        self.password = ''
        # mapping table for maketrans
        self.map_from = 'abcdefghijklmnopqrstuvwxyz0123456789'
        self.map_to =   'qazwsxedcrfvtgbyhnujmikolp9514738062'
        
    def get_filename( self):
        """Return the MD5 sum of the channel URL"""
        return md5.new( self.url).hexdigest()

    filename = property(fget=get_filename)

    def get_title( self):
        if self.override_title:
            return self.override_title
        elif not self.__title.strip():
            return self.url
        else:
            return self.__title

    def set_title( self, value):
        self.__title = value.strip()

    title = property(fget=get_title,
                     fset=set_title)

    def set_custom_title( self, custom_title):
        custom_title = custom_title.strip()

        if custom_title != self.__title:
            self.override_title = custom_title
        else:
            self.override_title = ''
    
    def get_localdb_channel( self):
        try:
            locdb_reader = readLocalDB( self.url)
            locdb_reader.parseXML( self.index_file)
            return locdb_reader.channel
        except:
            return podcastChannel( self.url, self.title, self.link, self.description)

    def set_localdb_channel( self, channel):
        if channel != None:
            try:
                log( 'Setting localdb channel data')
                writeLocalDB( self.index_file, channel)
            except:
                log( 'Cannot save channel in set_localdb_channel( %s)', channel.title)

    localdb_channel = property(fget=get_localdb_channel,
                               fset=set_localdb_channel)
    
    def set_metadata_from_localdb( self):
        log( 'Reading metadata from %s', self.index_file)
        libgpodder.getLock()
        self.copy_metadata_from( self.localdb_channel)
        libgpodder.releaseLock()

    def save_metadata_to_localdb( self):
        log( 'Saving metadata to %s', self.index_file)
        libgpodder.getLock()
        ch = self.localdb_channel
        ch.copy_metadata_from( self)
        self.localdb_channel = ch
        libgpodder.releaseLock()

    def copy_metadata_from( self, ch):
        # copy all metadata fields
        self.sync_to_devices = ch.sync_to_devices
        self.is_music_channel = ch.is_music_channel
        self.device_playlist_name = ch.device_playlist_name
        self.override_title = ch.override_title
        self.username = ch.username
        self.password = ch.password

    def newest_pubdate_downloaded( self):
        gl = libgpodder.gPodderLib()

        # Try DownloadHistory's entries first
        for episode in self:
            if gl.history_is_downloaded( episode.url):
                return episode.pubDate

        # If nothing found, do pubDate comparison
        pubdate = None
        for episode in self.localdb_channel:
            pubdate = episode.newer_pubdate( pubdate)
        return pubdate

    def get_new_episodes( self, download_status_manager = None):
        last_pubdate = self.newest_pubdate_downloaded()
        gl = libgpodder.gPodderLib()

        if not last_pubdate:
            return self[0:min(len(self),gl.default_new)]

        new_episodes = []

        for episode in self.get_all_episodes():
            # episode is older than newest downloaded
            if episode.compare_pubdate( last_pubdate) < 0:
                continue

            # episode has been downloaded before
            if self.is_downloaded( episode) or gl.history_is_downloaded( episode.url):
                continue

            # download is currently in progress
            if download_status_manager and download_status_manager.is_download_in_progress( episode.url):
                continue

            new_episodes.append( episode)

        return new_episodes

    def can_sort_by_pubdate( self):
        for episode in self:
            try:
                mktime_tz(parsedate_tz( episode.pubDate))
            except:
                log('Episode %s has non-parseable pubDate. Sorting disabled.', episode.title)
                return False
                can_sort = False

        return True
    
    def addDownloadedItem( self, item):
        # no multithreaded access
        libgpodder.getLock()
        localdb = self.index_file
        log( 'Local database: %s', localdb)

        self.downloaded = self.localdb_channel

        already_in_list = False
        # try to find the new item in the list
        for it in self.downloaded:
            if it.equals( item):
                already_in_list = True
                break

        # only append if not already in list
        if not already_in_list:
            self.downloaded.append( item)
            writeLocalDB( localdb, self.downloaded)

            # Update metadata on file (if possible and wanted)
            if libgpodder.gPodderLib().update_tags and tagging_supported():
                filename = self.getPodcastFilename( item.url)
                try:
                    update_metadata_on_file( filename, title = item.title, artist = self.title)
                except:
                    log('Error while calling update_metadata_on_file() :(')

        libgpodder.gPodderLib().history_mark_downloaded( item.url)
        
        if self.get_file_type( item) == 'torrent':
            torrent_filename = self.getPodcastFilename( item.url)
            destination_filename = self.get_torrent_filename( torrent_filename)
            libgpodder.gPodderLib().invoke_torrent( item.url, torrent_filename, destination_filename)
            
        libgpodder.releaseLock()
        return not already_in_list
    
    def printChannel( self):
        print '- Channel: "' + self.title + '"'
        for item in self:
            print '-- Item: "' + item.title + '"'

    def is_downloaded( self, item):
        return self.podcastFilenameExists( item.url)
        
    def is_played(self, item):
        return libgpodder.gPodderLib().history_is_played( item.url)

    def get_all_episodes( self):
        episodes = []
        added_urls = []
        added_guids = []

        # go through all episodes (both new and downloaded),
        # prefer already-downloaded (in localdb)
        for item in [] + self.localdb_channel + self:
            # skip items with the same guid (if it has a guid)
            if item.guid and item.guid in added_guids:
                continue

            # skip items with the same download url
            if item.url in added_urls:
                continue

            episodes.append( item)

            added_urls.append( item.url)
            if item.guid:
                added_guids.append( item.guid)

        episodes.sort( reverse = True)

        return episodes

    def items_liststore( self, want_color = True, downloading_callback = None, download_status_manager = None):
        """Return a gtk.ListStore containing episodes for this channel

        If want_color is True (the default), this will set special colors
        for already downloaded episodes and download-in-progress episodes.

        If downloading_callback is set, this should be a function that takes 
        the URL of the episodes and returns True if the episode is currently 
        being downloaded and False otherwise.
        """
        new_model = gtk.ListStore( gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_BOOLEAN, gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING)
        gl = libgpodder.gPodderLib()

        new_episodes = self.get_new_episodes( download_status_manager = download_status_manager)

        index = 1
        for item in self.get_all_episodes():
            played_icon = None
            if self.is_downloaded( item) and want_color:
                if not libgpodder.gPodderLib().history_is_played( item.url):
                    played_icon = gtk.STOCK_YES

                file_type = self.get_file_type( item)
                if file_type == 'audio':
                    status_icon = 'audio-x-generic'
                elif file_type == 'video':
                    status_icon = 'video-x-generic'
                elif file_type == 'torrent':
                    status_icon = 'applications-internet'
                else:
                    status_icon = 'unknown'
            elif downloading_callback and downloading_callback( item.url) and want_color:
                status_icon = gtk.STOCK_GO_DOWN
            elif libgpodder.gPodderLib().history_is_downloaded( item.url) and want_color:
                status_icon = gtk.STOCK_DELETE
            elif item.url in [ e.url for e in new_episodes ]:
                status_icon = gtk.STOCK_NEW
            else:
                status_icon = None
            new_iter = new_model.append()
            new_model.set( new_iter, 0, item.url)
            new_model.set( new_iter, 1, item.title)
            new_model.set( new_iter, 2, util.format_filesize( item.length))
            new_model.set( new_iter, 3, True)
            new_model.set( new_iter, 4, status_icon)
            new_model.set( new_iter, 5, item.cute_pubdate())
            new_model.set( new_iter, 6, item.one_line_description())
            new_model.set( new_iter, 7, item.description)
            new_model.set( new_iter, 8, played_icon)
            index += 1
        
        return new_model
    
    def find_episode( self, url):
        for item in self.get_all_episodes():
            if url == item.url:
                return item

        return None

    def downloadRss( self, force_update = True, callback_error = None, callback_is_cancelled = None):
        if callback_is_cancelled:
            if callback_is_cancelled() == True:
                return self.cache_file

        if not exists( self.cache_file) or force_update:
            # remove old cache file
            self.remove_cache_file()
            event = Event()
            download_thread = downloadThread( self.url, self.cache_file, event)
            download_thread.download()
            
            while not event.isSet():
                if callback_is_cancelled:
                    if callback_is_cancelled() == True:
                        download_thread.cancel()
                        self.restore_cache_file()
                event.wait( 0.2)

            # check if download was a success
            if not exists( self.cache_file):
                log('(downloadRss) Download failed! Trying to restore cache file..')
                restored = self.restore_cache_file()
                if callback_error:
                    if restored:
                        callback_error( _('Error downloading %s. Using cached file instead.') % ( unquote( self.url), ))
                    else:
                        callback_error( _('Error downloading %s.') % ( unquote( self.url), ))
                return restored
        
        return self.cache_file
    
    def get_save_dir(self):
        save_dir = os.path.join( libgpodder.gPodderLib().downloaddir, self.filename ) + '/'

        # Create save_dir if it does not yet exist
        if not util.make_directory( save_dir):
            log( '(libpodcasts) Could not create: %s', save_dir)

        return save_dir
    
    save_dir = property(fget=get_save_dir)

    def get_cache_file(self):
        return libgpodder.gPodderLib().cachedir + self.filename + '.xml'

    cache_file = property(fget=get_cache_file)

    def get_cache_backup_file( self):
        return libgpodder.gPodderLib().cachedir + self.filename + '.bak'

    cache_backup_file = property(fget=get_cache_backup_file)

    def remove_cache_file( self):
        if exists( self.cache_file):
            shutil.copyfile( self.cache_file, self.cache_backup_file)

        util.delete_file( self.cache_file)

    def restore_cache_file( self):
        if exists( self.cache_backup_file):
            shutil.copyfile( self.cache_backup_file, self.cache_file)
            log('Successfully restored cache file from old backup :)')
            return self.cache_file

        log('Could not restore cache file, sorry..')
        return None

    def remove_downloaded( self):
        shutil.rmtree( self.save_dir, True)
    
    def get_index_file(self):
        # gets index xml filename for downloaded channels list
        return os.path.join( self.save_dir, 'index.xml')
    
    index_file = property(fget=get_index_file)
    
    def get_cover_file( self):
        # gets cover filename for cover download cache
        return os.path.join( self.save_dir, 'cover')

    cover_file = property(fget=get_cover_file)

    def get_torrent_filename( self, torrent_file):
        header = open( torrent_file).readline()
        try:
            # A crummy way to see if we really are dealing with a torrent file
            # using index to find values like name and pieces which hopefully
            # only show up in torrent files (else raise a ValueError)
            testvar = header.index("6:pieces")
            name_length_pos = int(header.index("4:name")) + 6
            # Find the filename for fun + this will add some extra verification
            colon_pos = int(header.find(":",name_length_pos))
            name_length = int(header[name_length_pos:colon_pos]) + 1
            name = header[(colon_pos + 1):(colon_pos + name_length)]
            return name
        except:
            return None

    def get_file_type( self, item):
        types = {
                'audio': [ 'mp3', 'ogg', 'wav', 'wma', 'aac', 'm4a' ],
                'video': [ 'mp4', 'avi', 'mpg', 'mpeg', 'm4v', 'mov' ],
                'torrent': [ 'torrent' ]
        }
        extension = splitext( self.getPodcastFilename( item.url))[1][1:]

        # Torrent file detection
        if self.get_torrent_filename( self.getPodcastFilename( item.url)) != None:
            return 'torrent'

        for type in types:
            if extension in types[type]:
                return type
        
        return 'unknown'
    
    def getPodcastFilename( self, url):
        # strip question mark (and everything behind it), fix %20 errors
        filename = basename( url).replace( '%20', ' ')
	indexOfQuestionMark = filename.rfind( '?')
	if indexOfQuestionMark != -1:
	    filename = filename[:indexOfQuestionMark]
	# end strip questionmark
        extension = splitext( filename)[1].lower()

        return self.save_dir + md5.new(url).hexdigest() + extension
    
    def podcastFilenameExists( self, url):
        return exists( self.getPodcastFilename( url))
    
    def delete_episode_by_url(self, url):
        log( 'Delete %s', url)
        # no multithreaded access
        libgpodder.getLock()

        new_localdb = self.localdb_channel

        for item in new_localdb:
            if item.url == url:
                new_localdb.remove(item)

        self.localdb_channel = new_localdb

        # clean-up downloaded file
        if self.podcastFilenameExists( url):
            episode_filename = self.getPodcastFilename( url)
            util.delete_file( episode_filename)

        libgpodder.releaseLock()

    def obfuscate_password(self, password, unobfuscate = False):
        if unobfuscate:
            translation_table = string.maketrans(self.map_to + self.map_to.upper(), self.map_from + self.map_from.upper())
        else:
            translation_table = string.maketrans(self.map_from + self.map_from.upper(), self.map_to + self.map_to.upper())
        try:
            # For now at least, only ascii passwords will work, non-ascii passwords will be stored in plaintext :-(
            return string.translate(password.encode('ascii'), translation_table)
        except:
            return password
        
class podcastItem(object):
    """holds data for one object in a channel"""
    def __init__( self):
        self.url = ''
        self.title = ''
        self.length = 0
        self.mimetype = ''
        self.guid = ''
        self.description = ''
        self.link = ''
        self.pubDate = datetime.now().ctime()

    def one_line_description( self):
        lines = self.description.strip().splitlines()
        if not lines or lines[0] == '':
            return _('No description available')
        else:
            desc = lines[0].strip()
            if len( desc) > 84:
                return desc[:80] + '...'
            else:
                return desc

    def __cmp__( self, other):
        try:
            timestamp_self = int(mktime_tz( parsedate_tz( self.pubDate)))
            timestamp_other = int(mktime_tz( parsedate_tz( other.pubDate)))
        except:
            # by default, do as if this is not the same
            # this is here so that comparisons with None 
            # can be allowed (item != None -> True)
            return -1
        
        return timestamp_self - timestamp_other

    def compare_pubdate( self, pubdate):
        try:
            timestamp_self = int(mktime_tz( parsedate_tz( self.pubDate)))
        except:
            return -1

        try:
            timestamp_other = int(mktime_tz( parsedate_tz( pubdate)))
        except:
            return 1

        return timestamp_self - timestamp_other

    def newer_pubdate( self, pubdate = None):
        if self.compare_pubdate( pubdate) > 0:
            return self.pubDate
        else:
            return pubdate

    def cute_pubdate( self):
        seconds_in_a_day = 86400
        try:
            timestamp = int(mktime_tz( parsedate_tz( self.pubDate)))
        except:
            return _("(unknown)")
        diff = int((time()+1)/seconds_in_a_day) - int(timestamp/seconds_in_a_day)
        
        if diff == 0:
           return _("Today")
        if diff == 1:
           return _("Yesterday")
        if diff < 7:
            return str(datetime.fromtimestamp( timestamp).strftime( "%A"))
        
        return str(datetime.fromtimestamp( timestamp).strftime( "%x"))

    def calculate_filesize( self, channel):
        try:
            self.length = str(os.path.getsize( channel.getPodcastFilename( self.url)))
        except:
            log( 'Could not get filesize for %s.', self.url)
    
    def equals( self, other_item):
        if other_item == None:
            return False
        
        return self.url == other_item.url

    def get_title( self):
        return self.__title

    def set_title( self, value):
        self.__title = value.strip()

    title = property(fget=get_title,
                     fset=set_title)
        


class DownloadHistory( ListType):
    def __init__( self, filename):
        self.filename = filename
        try:
            self.read_from_file()
        except:
            log( 'Creating new history list.', sender = self)

    def read_from_file( self):
        for line in open( self.filename, 'r'):
            self.append( line.strip())

    def save_to_file( self):
        if len( self):
            fp = open( self.filename, 'w')
            for url in self:
                fp.write( url + "\n")
            fp.close()
            log( 'Wrote %d history entries.', len( self), sender = self)

    def add_item( self, data, autosave = True):
        affected = 0
        if data and type( data) is ListType:
            # Support passing a list of urls to this function
            for url in data:
                affected = affected + self.add_item( url, autosave = False)
        else:
            if data not in self:
                log( 'Adding: %s', data, sender = self)
                self.append( data)
                affected = affected + 1

        if affected and autosave:
            self.save_to_file()

        return affected


class PlaybackHistory( DownloadHistory):
    pass


def channelsToModel( channels, download_status_manager = None):
    new_model = gtk.ListStore( gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_INT, gobject.TYPE_STRING, gobject.TYPE_INT, gobject.TYPE_STRING, gobject.TYPE_INT, gobject.TYPE_STRING, gtk.gdk.Pixbuf)
    pos = 0
    
    for channel in channels:
        new_episodes = channel.get_new_episodes( download_status_manager = download_status_manager)
        count = len(channel)
        count_new = len(new_episodes)

        new_iter = new_model.append()
        new_model.set( new_iter, 0, channel.url)
        new_model.set( new_iter, 1, channel.title)

        new_model.set( new_iter, 2, count)
        if count_new == 0:
            new_model.set( new_iter, 3, '')
        elif count_new == 1:
            new_model.set( new_iter, 3, _('New episode: %s') % ( new_episodes[-1].title ) + ' ')
        else:
            new_model.set( new_iter, 3, _('%s new episodes') % count_new + ' ')

        if count_new:
            new_model.set( new_iter, 4, pango.WEIGHT_BOLD)
            new_model.set( new_iter, 5, str(count_new))
        else:
            new_model.set( new_iter, 4, pango.WEIGHT_NORMAL)
            new_model.set( new_iter, 5, '')

        new_model.set( new_iter, 6, pos)

        new_model.set( new_iter, 7, '%s\n<small>%s</small>' % ( saxutils.escape( channel.title), saxutils.escape( channel.description.split('\n')[0]), ))

        channel_cover_found = False
        if os.path.exists( channel.cover_file) and os.path.getsize(channel.cover_file) > 0:
            try:
                new_model.set( new_iter, 8, gtk.gdk.pixbuf_new_from_file_at_size( channel.cover_file, 32, 32))
                channel_cover_found = True
            except: 
                exctype, value = sys.exc_info()[:2]
                log( 'Could not convert icon file "%s", error was "%s"', channel.cover_file, value )

        if not channel_cover_found:
            iconsize = gtk.icon_size_from_name('channel-icon')
            if not iconsize:
                iconsize = gtk.icon_size_register('channel-icon',32,32)
            icon_theme = gtk.icon_theme_get_default()
            globe_icon_name = 'applications-internet'
            try:
                new_model.set( new_iter, 8, icon_theme.load_icon(globe_icon_name, iconsize, 0))
            except:
                log( 'Cannot load "%s" icon (using an old or incomplete icon theme?)', globe_icon_name)
                new_model.set( new_iter, 8, None)

        pos = pos + 1
    
    return new_model

