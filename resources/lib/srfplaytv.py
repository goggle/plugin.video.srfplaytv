# Copyright (C) 2018 Alexander Seiler
#
#
# This file is part of plugin.video.srfplaytv.
#
# plugin.video.srfplaytv is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# plugin.video.srfplaytv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with plugin.video.srfplaytv.
# If not, see <http://www.gnu.org/licenses/>.

import sys
import traceback

from urllib.parse import unquote_plus
from urllib.parse import parse_qsl

import xbmc
import urllib.request
import json
import datetime
import xbmcgui
import xbmcaddon
import xbmcplugin

import srgssr

ADDON_ID = "plugin.video.srfplaytv"
REAL_SETTINGS = xbmcaddon.Addon(id=ADDON_ID)
ADDON_NAME = REAL_SETTINGS.getAddonInfo("name")
ADDON_VERSION = REAL_SETTINGS.getAddonInfo("version")
DEBUG = REAL_SETTINGS.getSetting("Enable_Debugging") == "true"
CONTENT_TYPE = "videos"


class SRFPlayTV(srgssr.SRGSSR):
    def __init__(self):
        super(SRFPlayTV, self).__init__(int(sys.argv[1]), bu="srf", addon_id=ADDON_ID)



    def build_livetv_menu(self, sub_menu=None):
        """Fetches 24/7 channels and scheduled event livestreams.

        If sub_menu is None, renders 24/7 channels and folder links for sub-menus.
        If sub_menu is "sports" or "others", renders only that category of events.
        """
        headers = {'User-Agent': 'Mozilla/5.0'}

        # Case 1: Build the main root "Direct TV" page
        if sub_menu is None:
            # 1. Fetch available 24/7 livestreams
            livestreams_url = "https://www.srf.ch/play/v3/api/srf/production/tv-livestreams"
            try:
                req = urllib.request.Request(livestreams_url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    livestreams_data = json.loads(response.read().decode('utf-8'))
            except Exception as e:
                log(f"Failed to fetch live TV channels: {e}", xbmc.LOGERROR)
                return

            channels = livestreams_data.get("data", [])
            if not channels:
                return

            # 2. Fetch program guide for EPG data (enriched fallback)
            guide_url = "https://www.srf.ch/play/v3/api/srf/production/tv-program-guide"
            guide_by_channel = {}
            try:
                req = urllib.request.Request(guide_url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    guide_data = json.loads(response.read().decode('utf-8'))
                    for item in guide_data.get("data", []):
                        ch_id = item.get("channel", {}).get("id")
                        if ch_id:
                            guide_by_channel[ch_id] = item.get("programList", [])
            except Exception as e:
                log(f"Failed to fetch live TV program guide (falling back to channels-only): {e}", xbmc.LOGWARNING)

            now_utc = datetime.datetime.now(datetime.timezone.utc)

            # Add 24/7 linear channels
            for channel in channels:
                title = channel.get("title")
                urn = channel.get("livestreamUrn")
                img_url = channel.get("imageUrl")
                channel_id = channel.get("channelId")

                if title and urn:
                    current_show = ""
                    next_show = ""

                    program_list = guide_by_channel.get(channel_id, [])
                    for prog in program_list:
                        try:
                            start = datetime.datetime.fromisoformat(prog["startTime"].replace('Z', '+00:00'))
                            end = datetime.datetime.fromisoformat(prog["endTime"].replace('Z', '+00:00'))
                            if start <= now_utc <= end:
                                current_show = prog["title"]
                                break
                            elif start > now_utc:
                                if not next_show:
                                    formatted_start = start.astimezone().strftime("%H:%M")
                                    next_show = f"À suivre : {prog['title']} ({formatted_start})"
                        except Exception:
                            pass

                    status = current_show if current_show else next_show
                    display_name = f"{title} - {status}" if status else title

                    list_item = xbmcgui.ListItem(label=display_name)
                    list_item.setProperty("IsPlayable", "true")
                    if img_url:
                        list_item.setArt({"thumb": img_url})

                    plugin_url = self.build_url(mode=50, name=urn)
                    xbmcplugin.addDirectoryItem(self.handle, plugin_url, list_item, isFolder=False)

            # Add folder items for the two sub-directories
            sport_folder_url = self.build_url(mode=90, name="sports")
            sport_item = xbmcgui.ListItem(label=self.plugin_language(30101))
            sport_item.setArt({"icon": self.icon})
            xbmcplugin.addDirectoryItem(self.handle, sport_folder_url, sport_item, isFolder=True)

            others_folder_url = self.build_url(mode=90, name="others")
            others_item = xbmcgui.ListItem(label=self.plugin_language(30102))
            others_item.setArt({"icon": self.icon})
            xbmcplugin.addDirectoryItem(self.handle, others_folder_url, others_item, isFolder=True)

        # Case 2: Build a specific subdirectory ("sports" or "others") and its date folders/events
        else:
            sub_category, sep, date_filter = sub_menu.partition('_')

            # Fetch scheduled event livestreams with caching
            scheduled_url = (
                "https://il.srgssr.ch/integrationlayer/2.0/srf/mediaList/video/"
                "scheduledLivestreams?vector=portalplay&pageSize=100"
            )
            scheduled_events = []
            try:
                # Use SRGSSR's caching open_url to avoid redundant requests
                response_text = self.open_url(scheduled_url)
                if response_text:
                    scheduled_data = json.loads(response_text)
                    scheduled_events = scheduled_data.get("mediaList") or scheduled_data.get("data") or []
            except Exception as e:
                log(f"Failed to fetch scheduled livestreams: {e}", xbmc.LOGWARNING)

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            local_now = now_utc.astimezone()

            # Filter out past events
            active_and_upcoming = []
            for item in scheduled_events:
                try:
                    valid_to = datetime.datetime.fromisoformat(item["validTo"].replace('Z', '+00:00'))
                    if valid_to >= now_utc:
                        active_and_upcoming.append(item)
                except Exception:
                    pass

            # Filter by sub-category using the Integration Layer's "creatorUser" metadata attribute
            filtered_events = []
            for item in active_and_upcoming:
                is_sport = item.get("creatorUser") == "MMSport"

                if (sub_category == "sports" and is_sport) or (sub_category == "others" and not is_sport):
                    filtered_events.append(item)

            # Group filtered events by local date
            grouped_events = {}
            for item in filtered_events:
                try:
                    valid_from = datetime.datetime.fromisoformat(item["validFrom"].replace('Z', '+00:00'))
                    local_start = valid_from.astimezone()
                    local_date = local_start.date()
                    if local_date not in grouped_events:
                        grouped_events[local_date] = []
                    grouped_events[local_date].append((local_start, item))
                except Exception:
                    pass

            sorted_dates = sorted(grouped_events.keys())

            def folder_name(dato):
                weekdays = (
                    self.language(30060),  # Monday
                    self.language(30061),  # Tuesday
                    self.language(30062),  # Wednesday
                    self.language(30063),  # Thursday
                    self.language(30064),  # Friday
                    self.language(30065),  # Saturday
                    self.language(30066),  # Sunday
                )
                today = local_now.date()
                if dato == today:
                    return self.language(30058)  # Today
                elif dato == today - datetime.timedelta(days=1):
                    return self.language(30059)  # Yesterday
                return "%s, %s" % (weekdays[dato.weekday()], dato.strftime("%d.%m.%Y"))

            if not date_filter:
                # Build Date Folders
                for local_date in sorted_dates:
                    folder_label = folder_name(local_date)
                    folder_item = xbmcgui.ListItem(label=folder_label)
                    folder_item.setArt({"icon": self.icon})

                    target_name = f"{sub_category}_{local_date.isoformat()}"
                    folder_url = self.build_url(mode=90, name=target_name)
                    xbmcplugin.addDirectoryItem(self.handle, folder_url, folder_item, isFolder=True)
            else:
                # Render events chronologically for the selected date
                selected_date = datetime.date.fromisoformat(date_filter)
                events_on_day = grouped_events.get(selected_date, [])
                events_on_day.sort(key=lambda x: x[0])

                for start_time, item in events_on_day:
                    title = item.get("title")
                    urn = item.get("urn")
                    img_url = item.get("imageUrl")

                    if title and urn:
                        try:
                            valid_to = datetime.datetime.fromisoformat(item["validTo"].replace('Z', '+00:00'))
                            if start_time <= now_utc <= valid_to:
                                prefix = "[COLOR red][LIVE] [/COLOR]"
                            else:
                                prefix = f"[{start_time.strftime('%H:%M')}] "
                        except Exception:
                            prefix = ""

                        display_name = f"{prefix}{title}"
                        list_item = xbmcgui.ListItem(label=display_name)
                        list_item.setProperty("IsPlayable", "true")
                        if img_url:
                            list_item.setArt({"thumb": img_url})

                        plugin_url = self.build_url(mode=50, name=urn)
                        xbmcplugin.addDirectoryItem(self.handle, plugin_url, list_item, isFolder=False)



def log(msg, level=xbmc.LOGDEBUG):
    """
    Logs a message using Kodi's logging interface.

    Keyword arguments:
    msg   -- the message to log
    level -- the logging level
    """
    if DEBUG:
        if level == xbmc.LOGERROR:
            msg += " ," + traceback.format_exc()
    xbmc.log(ADDON_ID + "-" + ADDON_VERSION + "-" + msg, level)


def get_params():
    return dict(parse_qsl(sys.argv[2][1:]))


def run():
    """
    Run the plugin.
    """
    params = get_params()
    try:
        url = unquote_plus(params["url"])
    except Exception:
        url = None
    try:
        name = unquote_plus(params["name"])
    except Exception:
        name = None
    try:
        mode = int(params["mode"])
    except Exception:
        mode = None
    try:
        page_hash = unquote_plus(params["page_hash"])
    except Exception:
        page_hash = None
    try:
        page = unquote_plus(params["page"])
    except Exception:
        page = None

    log("Mode: " + str(mode))
    log("URL : " + str(url))
    log("Name: " + str(name))
    log("Page Hash: " + str(page_hash))
    log("Page: " + str(page))

    if mode is None:
        identifiers = [
            "All_Shows",
            "Favourite_Shows",
            "Newest_Favourite_Shows",
            "Homepage",
            "Topics",
            "Shows_By_Date",
            "Search",
            "SRF_YouTube",
        ]
        SRFPlayTV().menu_builder.build_main_menu(identifiers)
    elif mode == 10:
        SRFPlayTV().menu_builder.build_all_shows_menu()
    elif mode == 11:
        SRFPlayTV().menu_builder.build_favourite_shows_menu()
    elif mode == 12:
        SRFPlayTV().menu_builder.build_newest_favourite_menu(page=page)
    elif mode == 13:
        SRFPlayTV().menu_builder.build_topics_menu()
    elif mode == 17:
        SRFPlayTV().menu_builder.build_dates_overview_menu()
    elif mode == 19:
        SRFPlayTV().manage_favourite_shows()
    elif mode == 21:
        SRFPlayTV().menu_builder.build_episode_menu(name)
    elif mode == 24:
        SRFPlayTV().menu_builder.build_date_menu(name)
    elif mode == 60:
        SRFPlayTV().menu_builder.build_specific_date_menu(name)
    elif mode == 25:
        SRFPlayTV().menu_builder.pick_date()
    elif mode == 27:
        SRFPlayTV().menu_builder.build_search_menu()
    elif mode == 28:
        SRFPlayTV().menu_builder.build_search_media_menu(
            mode=mode, name=name, page=page, page_hash=page_hash
        )
    elif mode == 70:
        SRFPlayTV().menu_builder.build_recent_search_menu()
    elif mode == 30:
        SRFPlayTV().youtube_builder.build_youtube_channel_overview_menu(33)
    elif mode == 33:
        SRFPlayTV().youtube_builder.build_youtube_channel_menu(
            name, mode, page=page, page_token=page_hash
        )
    elif mode == 50:
        SRFPlayTV().player.play_video(name)
    elif mode == 100:
        SRFPlayTV().menu_builder.build_menu_by_urn(name)
    elif mode == 200:
        SRFPlayTV().menu_builder.build_homepage_menu()

    elif mode == 90:
        SRFPlayTV().build_livetv_menu(name)
    elif mode == 1000:
        SRFPlayTV().menu_builder.build_menu_apiv3(name, mode, page, page_hash)

    xbmcplugin.setContent(int(sys.argv[1]), CONTENT_TYPE)
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_LABEL)
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_TITLE)
    xbmcplugin.endOfDirectory(int(sys.argv[1]), cacheToDisc=True)
