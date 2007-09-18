# -*- coding: utf-8 -*-
#
# gPodder - A media aggregator and podcast client
# Copyright (C) 2005-2007 Thomas Perl <thp at perli.net>
#
# gPodder is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# gPodder is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from gpodder import util
from gpodder import download
from gpodder.liblogger import msg

from libpodcasts import load_channels
from libpodcasts import save_channels
from libpodcasts import podcastChannel

import time

import popen2
import urllib


def list_channels():
    for channel in load_channels( load_items = False):
        msg( 'channel', urllib.unquote( channel.url))


def add_channel( url):
    callback_error = lambda s: msg( 'error', s)

    url = util.normalize_feed_url( url)
    try:
        channel = podcastChannel.get_by_url( url, force_update = True)
    except:
        msg( 'error', _('Could not load feed from URL: %s'), urllib.unquote( url))
        return

    if channel:
        channels = load_channels( load_items = False)
        if channel.url in ( c.url for c in channels ):
            msg( 'error', _('Already added: %s'), urllib.unquote( url))
            return
        channels.append( channel)
        save_channels( channels)
        msg( 'add', urllib.unquote( url))
    else:
        msg( 'error', _('Could not add channel.'))


def del_channel( url):
    url = util.normalize_feed_url( url)

    channels = load_channels( load_items = False)
    keep_channels = []
    for channel in channels:
        if channel.url == url:
            msg( 'delete', urllib.unquote( channel.url))
        else:
            keep_channels.append( channel)

    if len(keep_channels) < len(channels):
        save_channels( keep_channels)
    else:
        msg( 'error', _('Could not remove channel.'))


def update():
    callback_url = lambda url: msg( 'update', urllib.unquote( url))
    callback_error = lambda s: msg( 'error', s)

    return load_channels( force_update = True, callback_url = callback_url, callback_error = callback_error)


def run():
    channels = update()

    for channel in channels:
       for episode in channel.get_new_episodes():
           msg( 'downloading', urllib.unquote( episode.url))
           # Calling run() calls the code in the current thread
           download.DownloadThread( channel, episode).run()

