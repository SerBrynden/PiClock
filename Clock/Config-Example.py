# -*- coding: utf-8 -*-
from PyQt5.QtGui import QColor

from GoogleMercatorProjection import LatLng

# LOCATION(S)
# Further radar configuration (zoom, marker location) can be
# completed under the RADAR section
primary_coordinates = 44.9764016, -93.2486732  # Change to your Lat/Lon

# Location for weather report
location = LatLng(primary_coordinates[0], primary_coordinates[1])
# Default radar location
radar_location = LatLng(primary_coordinates[0], primary_coordinates[1])

noaastream = 'http://www.urberg.net:8000/tim273/edina'
background = 'images/clockbackground-kevin.png'
squares1 = 'images/squares1-kevin.png'
squares2 = 'images/squares2-kevin.png'
icons = 'icons-lightblue'
textcolor = '#bef'
clockface = 'images/clockface3.png'
hourhand = 'images/hourhand.png'
minhand = 'images/minhand.png'
sechand = 'images/sechand.png'

# SlideShow
useslideshow = 0  # 1 to enable, 0 to disable
slide_time = 305  # in seconds, 3600 per hour
slides = 'images/slideshow'  # the path to your local images
slide_bg_color = '#000'  # https://htmlcolorcodes.com/  black #000

# Startup screen selection: 1 for screen 1 (clock), 2 for screen 2 (dual radar)
startup_screen = 1

digital = 0  # 1 = Digital Clock, 0 = Analog Clock

# Goes with light blue config (like the default one)
digitalcolor = '#50CBEB'
digitalformat = '{0:%I:%M\n%S %p}'  # Format of the digital clock face
digitalsize = 200

# The above example shows in this way:
#  https://github.com/n0bel/PiClock/blob/master/Documentation/Digital%20Clock%20v1.jpg
# (specifications of the time string are documented here:
#  https://docs.python.org/3/library/time.html#time.strftime)

# digitalformat = '{0:%I:%M}'
# digitalsize = 250
# The above example shows in this way:
# https://github.com/n0bel/PiClock/blob/master/Documentation/Digital%20Clock%20v2.jpg

digitalformat2 = '{0:%H:%M:%S}'  # Format of the digital time on second screen

# Map base style.
#
# If using Google Maps, map_base is the Google Static Maps map type:
#   'roadmap', 'satellite', 'terrain', or 'hybrid'
#
# If using Mapbox, map_base is the Mapbox classic style id:
#   'mapbox/satellite-streets-v12'
#   'mapbox/streets-v12'
#   'mapbox/outdoors-v12'
#   'mapbox/dark-v11'
#   'mapbox/cj5l80zrp29942rmtg0zctjto'  # Mapbox calls this map style 'Decimal'
#   'user-name/custom-style-id'
#
# For more Mapbox styles, see https://docs.mapbox.com/map-styles/guides/classic-styles/
# To create custom Mapbox style, sign-in at https://www.mapbox.com/mapbox-studio
#
# If no Mapbox API key is set, Google Maps are used and require Google API key.
# If a Mapbox API key is set, Mapbox is used.
# map_base = 'hybrid'  # Google map type
# map_base = 'mapbox/satellite-streets-v12'  # Mapbox classic style
map_base = 'serbrynden/cmtb05qoy000401sk73c24q61'  # Custom Mapbox dark map style without labels, roads, borders, etc.

# Mapbox overlay style.
#
# This is only used with Mapbox. Google Maps does not use the overlay layer.
# Leave blank when using a standard Mapbox style as the complete base map.
# Set this only when using a custom Mapbox base style that needs labels, roads,
# borders, etc. drawn above the radar layer.
map_overlay = 'serbrynden/cmtb066o1000l01snc11ne90m'

userainviewer = 0  # 0 = LibreWXR, 1 = RainViewer (free tier max zoom 7)

# A non-blank api key usually selects the weather service.  Open-Meteo.com doesn't
# use one, so having no keys selects it.  1 forces it even if you have keys.
useopenmeteo = 1
metric = 0  # 0 = English, 1 = Metric
radar_refresh = 10  # minutes
weather_refresh = 30  # minutes
# Wind in degrees instead of cardinal 0 = cardinal, 1 = degrees
wind_degrees = 0
# Override pressure units in millibars, mbar, instead of inches Mercury, inHg, (0 = inHg, 1 = mbar)
# or use metric setting from above
pressure_mbar = metric

# gives all text additional attributes using QT style notation
# example: fontattr = 'font-weight: bold; '
fontattr = ''

# These are to dim the radar images, if needed.
# see and try Config-Example-Bedside.py
dimcolor = QColor('#000000')
dimcolor.setAlpha(0)

# Optional Current conditions replaced with observations from a METAR station
# METAR is worldwide, provided mostly for pilots
# But data can be sparse outside US and Europe
# If you're close to an international airport, you should find something close
# Find the closest METAR station with the following URL
# https://www.aviationweather.gov
# scroll/zoom the map to find your closest station
# or look up the ICAO code here:
# https://airportcodes.aero/name
METAR = ''

# Language-specific wording
# OpenWeather Language code
#  (https://openweathermap.org/api/current?collection=current_forecast#multi)
Language = 'EN'

# The Python Locale for date/time (locale.setlocale)
#  '' for default Pi Setting
# Locales must be installed in your Pi. To check what is installed:
# locale -a
# to install locales
# sudo dpkg-reconfigure locales
DateLocale = ''

# Language-specific wording
LPressure = 'Pressure '
LHumidity = 'Humidity '
LWind = 'Wind '
Lgusting = ' gust '
LFeelslike = 'Feels like '
LPrecip1hr = ' Precip 1hr: '
LToday = 'Today: '
LSunRise = 'Sun Rise: '
LSet = ' Set: '
LMoonPhase = ' Moon: '
LInsideTemp = 'Inside Temp '
LRain = ' Rain: '
LSnow = ' Snow: '
Lmoon1 = 'New Moon'
Lmoon2 = 'Waxing Crescent'
Lmoon3 = 'First Quarter'
Lmoon4 = 'Waxing Gibbous'
Lmoon5 = 'Full Moon'
Lmoon6 = 'Waning Gibbous'
Lmoon7 = 'Third Quarter'
Lmoon8 = 'Waning Crescent'

# Language-specific terms for Open-Meteo.com weather conditions
Lom_code_map = {
    0: 'Clear',
    1: 'Mainly Clear',
    2: 'Partly Cloudy',
    3: 'Overcast',
    45: 'Fog',
    48: 'Freezing Fog',
    51: 'Light Drizzle',
    53: 'Drizzle',
    55: 'Heavy Drizzle',
    56: 'Light Freezing Drizzle',
    57: 'Freezing Drizzle',
    61: 'Light Rain',
    63: 'Rain',
    65: 'Heavy Rain',
    66: 'Light Freezing Rain',
    67: 'Freezing Rain',
    71: 'Light Snow',
    73: 'Snow',
    75: 'Heavy Snow',
    77: 'Snow Grains',
    80: 'Light Showers',
    81: 'Showers',
    82: 'Heavy Showers',
    85: 'Light Snow Showers',
    86: 'Snow Showers',
    95: 'Thunderstorm',
    96: 'Thunderstorm with Hail',
    99: 'Thunderstorm with Heavy Hail'
}

# Language-specific terms for Tomorrow.io weather conditions
Ltm_code_map = {
    0: 'Unknown',
    1000: 'Clear',
    1100: 'Mostly Clear',
    1101: 'Partly Cloudy',
    1102: 'Mostly Cloudy',
    1001: 'Cloudy',
    2000: 'Fog',
    2100: 'Light Fog',
    4000: 'Drizzle',
    4001: 'Rain',
    4200: 'Light Rain',
    4201: 'Heavy Rain',
    5000: 'Snow',
    5001: 'Flurries',
    5100: 'Light Snow',
    5101: 'Heavy Snow',
    6000: 'Freezing Drizzle',
    6001: 'Freezing Rain',
    6200: 'Light Freezing Rain',
    6201: 'Heavy Freezing Rain',
    7000: 'Ice Pellets',
    7101: 'Heavy Ice Pellets',
    7102: 'Light Ice Pellets',
    8000: 'Thunderstorm'
}

# RADAR
# By default, radar_location entered will be the
# center and marker of all radar images.
# To update centers/markers, change radar sections
# below the desired lat/lon as:
# -FROM-
# radar_location,
# -TO-
# LatLng(44.9764016,-93.2486732),

# screen 1, top radar
radar1 = {
    'center': radar_location,  # the center of your radar block
    'zoom': 7,  # this is a map zoom factor, bigger number = smaller area
    'basemap': globals().get('map_base', ''),  # Base map type or style
    'overlay': globals().get('map_overlay', ''),  # Mapbox custom style for labels, roads, and borders only
    'color': 7,  # radar color scheme: https://librewxr.net/docs/doc-viewer#color-schemes
    'smooth': 1,  # radar smoothing
    'snow': 1,  # display snow as different color
    'markers': (  # map markers can be overlaid
        {
            'visible': 1,  # 0 = hide marker, 1 = show marker
            'location': radar_location,
            'color': 'red',
            'size': 'small',
            'image': 'teardrop-dot',  # optional image from the markers folder
        },  # dangling comma is on purpose to add more markers
    )
}

# screen 1, bottom radar
radar2 = {
    'center': radar_location,
    'zoom': 5,
    'basemap': globals().get('map_base', ''),
    'overlay': globals().get('map_overlay', ''),
    'color': 7,
    'smooth': 1,
    'snow': 1,
    'markers': (
        {
            'visible': 1,
            'location': radar_location,
            'color': 'red',
            'size': 'small',
            'image': 'teardrop-dot',
        },
    )
}

# screen 2, left radar
radar3 = {
    'center': radar_location,
    'zoom': 7,
    'basemap': globals().get('map_base', ''),
    'overlay': globals().get('map_overlay', ''),
    'color': 7,
    'smooth': 1,
    'snow': 1,
    'markers': (
        {
            'visible': 1,
            'location': radar_location,
            'color': 'red',
            'size': 'small',
            'image': 'teardrop-dot',
        },
    )
}

# screen 2, right radar
radar4 = {
    'center': radar_location,
    'zoom': 4,
    'basemap': globals().get('map_base', ''),
    'overlay': globals().get('map_overlay', ''),
    'color': 7,
    'smooth': 1,
    'snow': 1,
    'markers': (
        {
            'visible': 1,
            'location': radar_location,
            'color': 'red',
            'size': 'small',
            'image': 'teardrop-dot',
        },
    )
}
