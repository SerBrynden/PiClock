# -*- coding: utf-8 -*-

import datetime
import json
import locale
import math
import os
import platform
import random
import re
import signal
import sys
import time
import traceback
from subprocess import Popen

import dateutil.parser
import pytz
import tzlocal
from PyQt5 import QtGui, QtCore, QtNetwork, QtWidgets
from PyQt5.QtCore import QUrl
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QImage, QFont
from PyQt5.QtGui import QPixmap, QBrush, QColor
from PyQt5.QtNetwork import QNetworkReply
from PyQt5.QtNetwork import QNetworkRequest
from tzfpy import get_tz

sys.dont_write_bytecode = True
# These local imports intentionally come after sys.dont_write_bytecode is set.
# noqa: E402 tells lint tools to allow these imports below executable code.
from GoogleMercatorProjection import get_corners, get_point, get_tile_xy, LatLng  # noqa: E402
import ApiKeys  # noqa: E402


# --- Daily log rotation (at local midnight), keeping PyQtPiClock.1.log ... .7.log ---
class _DailyRotatingLineLogger:
    def __init__(self, log_path: str, keep: int = 7, tee_to=None):
        self.log_path = os.path.abspath(log_path)
        self.keep = keep
        self.tee_to = tee_to  # optional stream (e.g., original stdout)
        self._tz = tzlocal.get_localzone()
        self._buf = ""
        self._cur_date = datetime.datetime.now(tz=self._tz).date()
        self._fh = None
        self._open_for_today(rotate_on_open=True)

    def _open_for_today(self, rotate_on_open: bool):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        if rotate_on_open and os.path.exists(self.log_path):
            self._rotate_files()
        self._fh = open(self.log_path, "a", encoding="utf-8", buffering=1)

    def _rotate_files(self):
        try:
            if self._fh:
                self._fh.flush()
                self._fh.close()
        except Exception:
            pass

        # shift .6 -> .7, ... .1 -> .2, current -> .1 (we always write to .1)
        for i in range(self.keep, 1, -1):
            src = self.log_path.replace(".1.log", f".{i - 1}.log")
            dst = self.log_path.replace(".1.log", f".{i}.log")
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.replace(src, dst)
            except OSError:
                pass

    def _maybe_rollover(self):
        today = datetime.datetime.now(tz=self._tz).date()
        if today != self._cur_date:
            self._cur_date = today
            self._rotate_files()
            self._open_for_today(rotate_on_open=False)

    def _timestamp_prefix(self) -> str:
        now = datetime.datetime.now(tz=self._tz)
        return now.strftime("%F %T.%f %Z (UTC%z) - ")

    def write(self, s: str):
        if not s:
            return
        self._maybe_rollover()
        self._buf += s

        while True:
            nl = self._buf.find("\n")
            if nl < 0:
                break
            line = self._buf[:nl]
            self._buf = self._buf[nl + 1:]

            out = self._timestamp_prefix() + line + "\n"
            try:
                self._fh.write(out)
            except Exception:
                pass

            if self.tee_to is not None:
                try:
                    self.tee_to.write(out)
                except Exception:
                    pass

    def flush(self):
        self._maybe_rollover()
        if self._buf:
            out = ""
            # flush partial line without forcing a newline
            try:
                out = self._timestamp_prefix() + self._buf
                self._fh.write(out)
            except Exception:
                pass
            if self.tee_to is not None:
                try:
                    self.tee_to.write(out)
                except Exception:
                    pass
            self._buf = ""
        try:
            if self._fh:
                self._fh.flush()
        except Exception:
            pass
        if self.tee_to is not None:
            try:
                self.tee_to.flush()
            except Exception:
                pass

    def close(self):
        try:
            self.flush()
        finally:
            try:
                if self._fh:
                    self._fh.close()
            except Exception:
                pass


def _setup_daily_log_if_enabled():
    if os.environ.get("PICLOCK_DAILY_LOG", "").strip() not in ("1", "true", "True", "yes", "YES"):
        return
    try:
        # Expect to be run from Clock/ (startup.sh does cd Clock)
        log_file = os.path.join(os.getcwd(), "PyQtPiClock.1.log")
        logger = _DailyRotatingLineLogger(log_file, keep=7, tee_to=None)
        sys.stdout = logger
        sys.stderr = logger
        import atexit
        atexit.register(logger.close)
    except Exception:
        # If anything goes wrong, fall back to normal stdout/stderr.
        pass


_setup_daily_log_if_enabled()


# --- end daily log rotation setup ---


class SunTimes:
    def __init__(self, lat, lng, tz):
        self.lat = lat
        self.lng = lng
        self.tz = tz

    def sunrise(self, when=None):
        if when is None:
            when = datetime.datetime.now(tz=tzlocal.get_localzone())
        # datetime at local coordinates
        when = when.astimezone(tz=self.tz)
        self.__preptime(when)
        self.__calc()
        # time part of sunrise at local coordinates
        sunrise_t = SunTimes.__timefromdecimalday(self.sunrise_t)
        # complete datetime of sunrise at local coordinates
        sunrise_dt = datetime.datetime.combine(when.date(), sunrise_t, when.tzinfo)
        # return datetime of sunrise in the designated system timezone
        return sunrise_dt.astimezone(tzlocal.get_localzone())

    def sunset(self, when=None):
        if when is None:
            when = datetime.datetime.now(tz=tzlocal.get_localzone())
        # datetime at local coordinates
        when = when.astimezone(tz=self.tz)
        self.__preptime(when)
        self.__calc()
        # time part of sunset at local coordinates
        sunset_t = SunTimes.__timefromdecimalday(self.sunset_t)
        # complete datetime of sunset at local coordinates
        sunset_dt = datetime.datetime.combine(when.date(), sunset_t, when.tzinfo)
        # return datetime of sunset in designated system timezone
        return sunset_dt.astimezone(tzlocal.get_localzone())

    @staticmethod
    def __timefromdecimalday(day):
        hours = 24.0 * day
        h = int(hours)
        minutes = (hours - h) * 60
        m = int(minutes)
        seconds = (minutes - m) * 60
        s = int(seconds)
        return datetime.time(hour=h, minute=m, second=s)

    def __preptime(self, when):
        # datetime days are numbered in the Gregorian calendar
        # while the calculations from NOAA are distributed as
        # OpenOffice spreadsheets with days numbered from
        # 1/1/1900. The difference are those numbers taken for
        # 18/12/2010
        self.day = when.toordinal() - (734124 - 40529)
        t = when.time()
        self.time = (t.hour + t.minute / 60.0 + t.second / 3600.0) / 24.0

        self.timezone = 0
        offset = when.utcoffset()
        if offset is not None:
            self.timezone = offset.seconds / 3600.0 + (offset.days * 24)

    def __calc(self):
        timezone = self.timezone  # in hours, east is positive
        longitude = self.lng  # in decimal degrees, east is positive
        latitude = self.lat  # in decimal degrees, north is positive

        time = self.time  # percentage past midnight, i.e., noon is 0.5
        day = self.day  # daynumber 1=1/1/1900

        j_day = day + 2415018.5 + time - timezone / 24  # Julian day
        j_cent = (j_day - 2451545) / 36525  # Julian century

        m_anon = 357.52911 + j_cent * (35999.05029 - 0.0001537 * j_cent)
        m_long = 280.46646 + j_cent * (36000.76983 + j_cent * 0.0003032) % 360
        eccent = 0.016708634 - j_cent * (0.000042037 + 0.0001537 * j_cent)
        m_obliq = (23 + (26 + ((21.448 - j_cent * (46.815 + j_cent *
                                                   (0.00059 - j_cent * 0.001813)))) / 60) / 60)
        obliq = (m_obliq + 0.00256 *
                 math.cos(math.radians(125.04 - 1934.136 * j_cent)))
        vary = (math.tan(math.radians(obliq / 2)) *
                math.tan(math.radians(obliq / 2)))
        s_eqcent = (math.sin(math.radians(m_anon)) *
                    (1.914602 - j_cent * (0.004817 + 0.000014 * j_cent)) +
                    math.sin(math.radians(2 * m_anon))
                    * (0.019993 - 0.000101 * j_cent) +
                    math.sin(math.radians(3 * m_anon)) * 0.000289)
        s_truelong = m_long + s_eqcent
        s_applong = (s_truelong - 0.00569 - 0.00478 *
                     math.sin(math.radians(125.04 - 1934.136 * j_cent)))
        declination = (math.degrees(math.asin(math.sin(math.radians(obliq)) *
                                              math.sin(math.radians(s_applong)))))

        eqtime = (4 * math.degrees(vary * math.sin(2 * math.radians(m_long)) -
                                   2 * eccent * math.sin(math.radians(m_anon)) + 4 * eccent *
                                   vary * math.sin(math.radians(m_anon)) *
                                   math.cos(2 * math.radians(m_long)) - 0.5 * vary * vary *
                                   math.sin(4 * math.radians(m_long)) - 1.25 * eccent * eccent *
                                   math.sin(2 * math.radians(m_anon))))

        hourangle0 = (math.cos(math.radians(90.833)) /
                      (math.cos(math.radians(latitude)) *
                       math.cos(math.radians(declination))) -
                      math.tan(math.radians(latitude)) *
                      math.tan(math.radians(declination)))

        self.solarnoon_t = (720 - 4 * longitude - eqtime + timezone * 60) / 1440
        # sun never sets
        if hourangle0 > 1.0:
            self.sunrise_t = 0.0
            self.sunset_t = 1.0 - 1.0 / 86400.0
            return
        if hourangle0 < -1.0:
            self.sunrise_t = 0.0
            self.sunset_t = 0.0
            return

        hourangle = math.degrees(math.acos(hourangle0))

        self.sunrise_t = self.solarnoon_t - hourangle * 4 / 1440
        self.sunset_t = self.solarnoon_t + hourangle * 4 / 1440


# https://gist.github.com/miklb/ed145757971096565723
def moon_phase(dt=None):
    if dt is None:
        dt = datetime.datetime.now()
    diff = dt - datetime.datetime(2001, 1, 1)
    days = float(diff.days) + (float(diff.seconds) / 86400.0)
    lunations = 0.20439731 + float(days) * 0.03386319269
    return lunations % 1.0


def tick():
    global lastmin, lastday, lasttimestr
    global pdy
    global daytime, sunrise, sunset

    now = datetime.datetime.now(tz=tzlocal.get_localzone())
    if Config.digital:
        timestr = Config.digitalformat.format(now)
        if Config.digitalformat.find('%I') > -1:
            if timestr[0] == '0':
                timestr = timestr[1:99]
        if lasttimestr != timestr:
            clockface.setText(timestr.lower())
        lasttimestr = timestr
    else:
        angle = now.second * 6
        ts = secpixmap.size()
        secpixmap2 = secpixmap.transformed(
            QtGui.QTransform().scale(
                float(clockrect.width()) / ts.height(),
                float(clockrect.height()) / ts.height()
            ).rotate(angle),
            Qt.SmoothTransformation
        )
        sechand.setPixmap(secpixmap2)
        ts = secpixmap2.size()
        sechand.setGeometry(
            int(clockrect.center().x() - ts.width() / 2),
            int(clockrect.center().y() - ts.height() / 2),
            ts.width(),
            ts.height()
        )
        if now.minute != lastmin:
            angle = now.minute * 6
            ts = minpixmap.size()
            minpixmap2 = minpixmap.transformed(
                QtGui.QTransform().scale(
                    float(clockrect.width()) / ts.height(),
                    float(clockrect.height()) / ts.height()
                ).rotate(angle),
                Qt.SmoothTransformation
            )
            minhand.setPixmap(minpixmap2)
            ts = minpixmap2.size()
            minhand.setGeometry(
                int(clockrect.center().x() - ts.width() / 2),
                int(clockrect.center().y() - ts.height() / 2),
                ts.width(),
                ts.height()
            )

            angle = ((now.hour % 12) + now.minute / 60.0) * 30.0
            ts = hourpixmap.size()
            hourpixmap2 = hourpixmap.transformed(
                QtGui.QTransform().scale(
                    float(clockrect.width()) / ts.height(),
                    float(clockrect.height()) / ts.height()
                ).rotate(angle),
                Qt.SmoothTransformation
            )
            hourhand.setPixmap(hourpixmap2)
            ts = hourpixmap2.size()
            hourhand.setGeometry(
                int(clockrect.center().x() - ts.width() / 2),
                int(clockrect.center().y() - ts.height() / 2),
                ts.width(),
                ts.height()
            )

    dy = Config.digitalformat2.format(now)
    if Config.digitalformat2.find('%I') > -1:
        if dy[0] == '0':
            dy = dy[1:99]
    if dy != pdy:
        pdy = dy
        datey2.setText(dy)

    if now.minute != lastmin:
        lastmin = now.minute
        if sunrise <= now <= sunset:
            daytime = True
        else:
            daytime = False

    if now.day != lastday:
        lastday = now.day
        # date
        sup = 'th'
        if now.day == 1 or now.day == 21 or now.day == 31:
            sup = 'st'
        if now.day == 2 or now.day == 22:
            sup = 'nd'
        if now.day == 3 or now.day == 23:
            sup = 'rd'
        if Config.DateLocale != '':
            sup = ''
        ds = '{0:%A %B} {0.day}<sup>{1}</sup> {0.year}'.format(now, sup)
        ds2 = '{0:%a %b} {0.day}<sup>{1}</sup> {0.year}'.format(now, sup)
        datex.setText(ds)
        datex2.setText(ds2)
        dt = datetime.datetime.now(tz=tzlocal.get_localzone())
        sunrise = sun.sunrise(dt)
        sunset = sun.sunset(dt)
        bottomtext = ''
        bottomtext += (Config.LSunRise +
                       '{0:%H:%M}'.format(sunrise) +
                       Config.LSet +
                       '{0:%H:%M}'.format(sunset))
        bottomtext += (Config.LMoonPhase + phase(moon_phase()))
        bottom.setText(bottomtext)


def tempfinished():
    if tempreply.error() != QNetworkReply.NoError:
        return
    tempstr = str(tempreply.readAll(), 'utf-8')
    try:
        tempdata = json.loads(tempstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from localhost: ' + tempstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if tempdata['temp'] == '':
        return
    if Config.metric:
        s = Config.LInsideTemp + '%.1f' % tempf2tempc(float(tempdata['temp'])) + u'°C'
        if tempdata['temps']:
            if len(tempdata['temps']) > 1:
                s = ''
                for tk in tempdata['temps']:
                    s += ' ' + tk + ': ' + '%.1f' % tempf2tempc(float(tempdata['temps'][tk])) + u'°C'
    else:
        s = Config.LInsideTemp + tempdata['temp'] + u'°F'
        if tempdata['temps']:
            if len(tempdata['temps']) > 1:
                s = ''
                for tk in tempdata['temps']:
                    s += ' ' + tk + ': ' + tempdata['temps'][tk] + u'°F'
    temp.setText(s)


def safeurl(url):
    """Remove API keys from URLs for printing to screen or logging."""
    return re.sub(r'((?:apikey|appid|key|access_token)=)[^&]*',
                  r'\1<key>', url)


def tempf2tempc(f):
    return (f - 32) / 1.8  # temperature degrees Fahrenheit to degrees Celsius


def mph2kph(f):
    return f * 1.609  # speed MPH to km/h


def mbar2inhg(f):
    return f / 33.864  # pressure millibars to inHg


def inhg2mbar(f):
    return f * 33.864  # pressure inHg to millibars


def inches2mm(f):
    return f * 25.4  # height inches to millimeters


def mm2inches(f):
    return f / 25.4  # height millimeters to inches


def phase(f):
    pp = Config.Lmoon1  # 'New Moon'
    if f > 0.9375:
        pp = Config.Lmoon1  # 'New Moon'
    elif f > 0.8125:
        pp = Config.Lmoon8  # 'Waning Crescent'
    elif f > 0.6875:
        pp = Config.Lmoon7  # 'Third Quarter'
    elif f > 0.5625:
        pp = Config.Lmoon6  # 'Waning Gibbous'
    elif f > 0.4375:
        pp = Config.Lmoon5  # 'Full Moon'
    elif f > 0.3125:
        pp = Config.Lmoon4  # 'Waxing Gibbous'
    elif f > 0.1875:
        pp = Config.Lmoon3  # 'First Quarter'
    elif f > 0.0625:
        pp = Config.Lmoon2  # 'Waxing Crescent'
    return pp


def bearing(f):
    wd = 'N'
    if f > 22.5:
        wd = 'NE'
    if f > 67.5:
        wd = 'E'
    if f > 112.5:
        wd = 'SE'
    if f > 157.5:
        wd = 'S'
    if f > 202.5:
        wd = 'SW'
    if f > 247.5:
        wd = 'W'
    if f > 292.5:
        wd = 'NW'
    if f > 337.5:
        wd = 'N'
    return wd


def gettemp():
    global tempreply
    host = 'localhost'
    if platform.uname()[1] == 'KW81':
        host = 'piclock.local'  # this is here just for testing
    r = QUrl('http://' + host + ':48213/temp')
    r = QNetworkRequest(r)
    tempreply = manager.get(r)
    tempreply.finished.connect(tempfinished)


owm_code_icons = {
    '01d': 'clear-day',
    '02d': 'partly-cloudy-day',
    '03d': 'partly-cloudy-day',
    '04d': 'cloudy',
    '09d': 'rain',
    '10d': 'rain',
    '11d': 'thunderstorm',
    '13d': 'snow',
    '50d': 'fog',
    '01n': 'clear-night',
    '02n': 'partly-cloudy-night',
    '03n': 'partly-cloudy-night',
    '04n': 'cloudy',
    '09n': 'rain',
    '10n': 'rain',
    '11n': 'thunderstorm',
    '13n': 'snow',
    '50n': 'fog'
}


def wxfinished_owm_onecall():
    """Get current weather conditions and forecast in one call from OpenWeatherMap.org"""
    global owmonecall

    attribution.setText('OpenWeatherMap.org')
    attribution2.setText('OpenWeatherMap.org')

    wxstr = str(wxreply.readAll(), 'utf-8')

    try:
        wxdata = json.loads(wxstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.openweathermap.org: ' + wxstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'cod' in wxdata:
        print('WARNING: Response from api.openweathermap.org: ' + str(wxdata['cod']) + ' - ' + str(wxdata['message']))
        if wxdata['cod'] == 401:  # Invalid API
            print('WARNING: OpenWeather One Call failed...')
            print('WARNING: Falling back to separate OpenWeather calls for current weather conditions and forecast')
            owmonecall = False
            getwx_owm()
        return

    if not hasMetar:
        f = wxdata['current']
        dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
        icon = f['weather'][0]['icon']
        icon = owm_code_icons[icon]
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, icon + '.png'))
        wxicon.setPixmap(wxiconpixmap.scaled(
            wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wxicon2.setPixmap(wxiconpixmap.scaled(
            wxicon.width(),
            wxicon.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wxdesc.setText(f['weather'][0]['description'].title())
        wxdesc2.setText(f['weather'][0]['description'].title())

        if Config.wind_degrees:
            wd = str(f['wind_deg']) + u'°'
        else:
            wd = bearing(f['wind_deg'])

        if Config.metric:
            temper.setText('%.1f' % (tempf2tempc(f['temp'])) + u'°C')
            temper2.setText('%.1f' % (tempf2tempc(f['temp'])) + u'°C')
            w = (Config.LWind + wd + ' ' + '%.1f' % (mph2kph(f['wind_speed'])) + 'km/h')
            if 'wind_gust' in f:
                w += (Config.Lgusting + '%.1f' % (mph2kph(f['wind_gust'])) + 'km/h')
            feelslike.setText(Config.LFeelslike + '%.1f' % (tempf2tempc(f['feels_like'])) + u'°C')
        else:
            temper.setText('%.1f' % (f['temp']) + u'°F')
            temper2.setText('%.1f' % (f['temp']) + u'°F')
            w = (Config.LWind + wd + ' ' + '%.1f' % (f['wind_speed']) + 'mph')
            if 'wind_gust' in f:
                w += (Config.Lgusting + '%.1f' % (f['wind_gust']) + 'mph')
            feelslike.setText(Config.LFeelslike + '%.1f' % (f['feels_like']) + u'°F')

        if Config.pressure_mbar:
            press.setText(Config.LPressure + '%.1f' % f['pressure'] + 'mbar')
        else:
            press.setText(Config.LPressure + '%.2f' % mbar2inhg(f['pressure']) + 'inHg')

        wind.setText(w)
        humidity.setText(Config.LHumidity + '%.0f%%' % (f['humidity']))
        wdate.setText('{0:%H:%M %Z}'.format(dt))

    for i in range(0, 3):
        f = wxdata['hourly'][i * 3 + 2]
        dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
        fl = forecast[i]
        wicon = f['weather'][0]['icon']
        wicon = owm_code_icons[wicon]
        icon = fl.findChild(QtWidgets.QLabel, 'icon')
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(),
            icon.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, 'wx')
        day = fl.findChild(QtWidgets.QLabel, 'day')
        day.setText('{0:%a %I:%M%p}'.format(dt))
        s = ''
        pop = 0
        ptype = ''
        paccum = 0
        if 'pop' in f:
            pop = float(f['pop']) * 100.0
        if 'snow' in f:
            ptype = 'snow'
            paccum = float(f['snow']['1h'])
        if 'rain' in f:
            ptype = 'rain'
            paccum = float(f['rain']['1h'])

        if pop > 0.0 or ptype != '':
            s += '%.0f' % pop + '% '
        if Config.metric:
            if ptype == 'snow':
                if paccum > 0.1:
                    s += Config.LSnow + '%.1f' % paccum + 'mm/hr '
            else:
                if paccum > 0.1:
                    s += Config.LRain + '%.1f' % paccum + 'mm/hr '
            s += '%.0f' % tempf2tempc(f['temp']) + u'°C'
        else:
            if ptype == 'snow':
                if paccum > 2.54:
                    s += Config.LSnow + '%.1f' % mm2inches(paccum) + 'in/hr '
            else:
                if paccum > 2.54:
                    s += Config.LRain + '%.1f' % mm2inches(paccum) + 'in/hr '
            s += '%.0f' % (f['temp']) + u'°F'

        wx.setStyleSheet('#wx { font-size: ' + str(int(19 * xscale * Config.fontmult)) + 'px; }')
        wx.setText(f['weather'][0]['description'].title() + '\n' + s)

    dt = datetime.datetime.fromtimestamp(int(wxdata['daily'][0]['dt'])).astimezone(tzlocal.get_localzone())
    date_offset = 0
    if dt.date() < datetime.datetime.now().date():
        date_offset = 1

    for i in range(3, 9):
        f = wxdata['daily'][i - 3 + date_offset]
        dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
        wicon = f['weather'][0]['icon']
        wicon = owm_code_icons[wicon]
        fl = forecast[i]
        icon = fl.findChild(QtWidgets.QLabel, 'icon')
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(),
            icon.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, 'wx')
        day = fl.findChild(QtWidgets.QLabel, 'day')
        day.setText('{0:%a %m/%d}'.format(dt))
        s = ''
        pop = 0
        ptype = ''
        paccum = 0
        if 'pop' in f:
            pop = float(f['pop']) * 100.0
        if 'rain' in f:
            ptype = 'rain'
            paccum = float(f['rain'])
        if 'snow' in f:
            ptype = 'snow'
            paccum = float(f['snow'])

        if pop > 0.05 or ptype != '':
            s += '%.0f' % pop + '% '
        if Config.metric:
            if ptype == 'snow':
                if paccum > 0.1:
                    s += Config.LSnow + '%.1f' % paccum + 'mm '
            else:
                if paccum > 0.1:
                    s += Config.LRain + '%.1f' % paccum + 'mm '
            s += '%.0f' % tempf2tempc(f['temp']['max']) + '/' + \
                 '%.0f' % tempf2tempc(f['temp']['min']) + u'°C'
        else:
            if ptype == 'snow':
                if paccum > 2.54:
                    s += Config.LSnow + '%.1f' % mm2inches(paccum) + 'in '
            else:
                if paccum > 2.54:
                    s += Config.LRain + '%.1f' % mm2inches(paccum) + 'in '
            s += '%.0f' % f['temp']['max'] + '/' + \
                 '%.0f' % f['temp']['min'] + u'°F'

        wx.setStyleSheet('#wx { font-size: ' + str(int(19 * xscale * Config.fontmult)) + 'px; }')
        wx.setText(f['weather'][0]['description'].title() + '\n' + s)


def wxfinished_owm_current():
    """Get current weather conditions from OpenWeatherMap.org"""
    wxstr = str(wxreplyc.readAll(), 'utf-8')

    try:
        wxdata = json.loads(wxstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.openweathermap.org: ' + wxstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'message' in wxdata:
        print('ERROR: Response from api.openweathermap.org: ' + str(wxdata['cod']) + ' - ' + str(wxdata['message']))
        return

    f = wxdata
    dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
    icon = f['weather'][0]['icon']
    icon = owm_code_icons[icon]
    wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, icon + '.png'))
    wxicon.setPixmap(wxiconpixmap.scaled(
        wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxicon2.setPixmap(wxiconpixmap.scaled(
        wxicon.width(),
        wxicon.height(),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxdesc.setText(f['weather'][0]['description'].title())
    wxdesc2.setText(f['weather'][0]['description'].title())

    if Config.wind_degrees:
        wd = str(f['wind']['deg']) + u'°'
    else:
        wd = bearing(f['wind']['deg'])

    if Config.metric:
        temper.setText('%.1f' % (tempf2tempc(f['main']['temp'])) + u'°C')
        temper2.setText('%.1f' % (tempf2tempc(f['main']['temp'])) + u'°C')
        w = (Config.LWind + wd + ' ' + '%.1f' % (mph2kph(f['wind']['speed'])) + 'km/h')
        if 'gust' in f['wind']:
            w += (Config.Lgusting + '%.1f' % (mph2kph(f['wind']['gust'])) + 'km/h')
        feelslike.setText(Config.LFeelslike + '%.1f' % (tempf2tempc(f['main']['feels_like'])) + u'°C')
    else:
        temper.setText('%.1f' % (f['main']['temp']) + u'°F')
        temper2.setText('%.1f' % (f['main']['temp']) + u'°F')
        w = (Config.LWind + wd + ' ' + '%.1f' % (f['wind']['speed']) + 'mph')
        if 'gust' in f['wind']:
            w += (Config.Lgusting + '%.1f' % (f['wind']['gust']) + 'mph')
        feelslike.setText(Config.LFeelslike + '%.1f' % (f['main']['feels_like']) + u'°F')

    if Config.pressure_mbar:
        press.setText(Config.LPressure + '%.1f' % f['main']['pressure'] + 'mbar')
    else:
        press.setText(Config.LPressure + '%.2f' % mbar2inhg(f['main']['pressure']) + 'inHg')

    wind.setText(w)
    humidity.setText(Config.LHumidity + '%.0f%%' % (f['main']['humidity']))
    wdate.setText('{0:%H:%M %Z}'.format(dt))


def wxfinished_owm_forecast():
    """Get the weather forecast from OpenWeatherMap.org"""
    attribution.setText('OpenWeatherMap.org')
    attribution2.setText('OpenWeatherMap.org')

    wxstr = str(wxreplyf.readAll(), 'utf-8')

    try:
        wxdata = json.loads(wxstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.openweathermap.org: ' + wxstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'message' in wxdata:
        if wxdata['message']:  # OWM forecast normally includes message of 0... if not 0 or text, print error and return
            print('ERROR: Response from api.openweathermap.org: ' + str(wxdata['cod']) + ' - ' + str(wxdata['message']))
            return

    for i in range(0, 3):
        f = wxdata['list'][i]
        dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
        fl = forecast[i]
        wicon = f['weather'][0]['icon']
        wicon = owm_code_icons[wicon]
        icon = fl.findChild(QtWidgets.QLabel, "icon")
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(),
            icon.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, "wx")
        day = fl.findChild(QtWidgets.QLabel, "day")
        day.setText("{0:%a %I:%M%p}".format(dt))
        f2 = f['main']
        s = ''
        pop = 0
        ptype = ''
        paccum = 0
        if 'pop' in f:
            pop = float(f['pop']) * 100.0
        if 'snow' in f:
            ptype = 'snow'
            paccum = float(f['snow']['3h'])
        if 'rain' in f:
            ptype = 'rain'
            paccum = float(f['rain']['3h'])

        paccum = paccum / 3.0

        if pop >= 0.1:
            s += '%.0f' % pop + '% '
        if Config.metric:
            if ptype == 'snow':
                if paccum > 0.1:
                    s += Config.LSnow + '%.1f' % paccum + 'mm/hr '
            else:
                if paccum > 0.1:
                    s += Config.LRain + '%.1f' % paccum + 'mm/hr '
            s += '%.0f' % tempf2tempc(f2['temp']) + u'°C'
        else:
            if ptype == 'snow':
                if paccum > 2.54:
                    s += Config.LSnow + '%.1f' % mm2inches(paccum) + 'in/hr '
            else:
                if paccum > 2.54:
                    s += Config.LRain + '%.1f' % mm2inches(paccum) + 'in/hr '
            s += '%.0f' % (f2['temp']) + u'°F'

        wx.setStyleSheet("#wx { font-size: " + str(int(19 * xscale * Config.fontmult)) + "px; }")
        wx.setText(f['weather'][0]['description'].title() + "\n" + s)

    # find 6am in the current timezone (weather day is 6am to 6am next day)
    dx = datetime.datetime.now(tz=tzlatlng)
    dx6am = tzlatlng.localize(datetime.datetime(dx.year, dx.month, dx.day, 6, 0, 0))
    dx6amnext = dx6am + datetime.timedelta(seconds=86399)

    for i in range(3, 9):  # target forecast box
        s = ''
        fl = forecast[i]
        wx = fl.findChild(QtWidgets.QLabel, "wx")
        day = fl.findChild(QtWidgets.QLabel, "day")
        icon = fl.findChild(QtWidgets.QLabel, "icon")
        setday = True
        has_forecast = False
        xpop = 0.0  # max
        rpaccum = 0.0  # total rain
        spaccum = 0.0  # total snow
        xmintemp = 9999  # min
        xmaxtemp = -9999  # max
        ldesc = []
        licon = []

        for f in wxdata['list']:
            dt = datetime.datetime.fromtimestamp(int(f['dt'])).astimezone(tzlocal.get_localzone())
            if dx6am <= dt <= dx6amnext:
                if setday:
                    setday = False
                    day.setText("{0:%a %m/%d}".format(dt))
                pop = 0.0
                if 'pop' in f:
                    pop = float(f['pop']) * 100.0
                if 'rain' in f:
                    paccum = float(f['rain']['3h'])
                    rpaccum += paccum
                if 'snow' in f:
                    paccum = float(f['snow']['3h'])
                    spaccum += paccum
                if pop > xpop:
                    xpop = pop
                tx = float(f['main']['temp'])
                if tx > xmaxtemp:
                    xmaxtemp = tx
                if tx < xmintemp:
                    xmintemp = tx
                has_forecast = True
                ldesc.append(f['weather'][0]['description'].title())
                licon.append(f['weather'][0]['icon'])

        if xpop > 0.1:
            s += '%.0f' % xpop + '% '

        if Config.metric:
            if spaccum > 0.1:
                s += Config.LSnow + '%.1f' % spaccum + 'mm '
            if rpaccum > 0.1:
                s += Config.LRain + '%.1f' % rpaccum + 'mm '
            s += '%.0f' % tempf2tempc(xmaxtemp) + '/' + \
                 '%.0f' % tempf2tempc(xmintemp) + u'°C'
        else:
            if spaccum > 2.54:
                s += Config.LSnow + '%.1f' % mm2inches(spaccum) + 'in '
            if rpaccum > 2.54:
                s += Config.LRain + '%.1f' % mm2inches(rpaccum) + 'in '
            s += '%.0f' % xmaxtemp + '/' + \
                 '%.0f' % xmintemp + u'°F'

        # when current time is shortly after midnight
        # there may not be any forecast after 6am for the final day
        if has_forecast:
            wicon = getmost(licon)
            wdesc = getmost(ldesc)
            wx.setStyleSheet("#wx { font-size: " + str(int(19 * xscale * Config.fontmult)) + "px; }")
            wx.setText(wdesc + "\n" + s)
            wicon = owm_code_icons[wicon]
            wicon = wicon.replace('-night', '-day')
            wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
            icon.setPixmap(wxiconpixmap.scaled(
                icon.width(),
                icon.height(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation))

        dx6am += datetime.timedelta(1)
        dx6amnext += datetime.timedelta(1)


def getwx_owm():
    """Get weather from OpenWeatherMap.org"""
    global wxreply, wxreplyc, wxreplyf

    if not hasattr(ApiKeys, 'owmapi'):
        return

    owmapi = ApiKeys.owmapi

    # try OWM One Call once, if it fails, then we go to two calls (current weather and forecast)
    if owmonecall:
        wxurl = 'https://api.openweathermap.org/data/3.0/onecall?appid=' + \
                owmapi
    else:
        wxurl = 'https://api.openweathermap.org/data/2.5/forecast?appid=' + \
                owmapi

    wxurl += "&lat=" + str(Config.location.lat) + \
             '&lon=' + str(Config.location.lng)
    wxurl += '&units=imperial&lang=' + Config.Language.lower()
    wxurl += '&r=' + str(random.random())

    if owmonecall:
        print('INFO: getting OpenWeather One Call: ' + safeurl(wxurl))
    else:
        print('INFO: getting OpenWeather forecast: ' + safeurl(wxurl))

    r = QUrl(wxurl)
    r = QNetworkRequest(r)

    if owmonecall:
        wxreply = manager.get(r)
        wxreply.finished.connect(wxfinished_owm_onecall)
    else:
        wxreplyf = manager.get(r)
        wxreplyf.finished.connect(wxfinished_owm_forecast)

    if not hasMetar and not owmonecall:
        wxurl = 'https://api.openweathermap.org/data/2.5/weather?appid=' + \
                owmapi
        wxurl += "&lat=" + str(Config.location.lat) + \
                 '&lon=' + str(Config.location.lng)
        wxurl += '&units=imperial&lang=' + Config.Language.lower()
        wxurl += '&r=' + str(random.random())
        print('INFO: getting OpenWeather current conditions: ' + safeurl(wxurl))
        r = QUrl(wxurl)
        r = QNetworkRequest(r)
        wxreplyc = manager.get(r)
        wxreplyc.finished.connect(wxfinished_owm_current)


def getmost(a):
    b = dict((i, a.count(i)) for i in a)  # list to key and counts
    # print('INFO:', 'getmost', b)
    c = sorted(b, key=b.get)  # sort by counts
    return c[-1]  # get last (most counted) item


tm_code_map = {
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

tm_code_icons = {
    0: 'Unknown',
    1000: 'clear-day',
    1100: 'partly-cloudy-day',
    1101: 'partly-cloudy-day',
    1102: 'partly-cloudy-day',
    1001: 'cloudy',
    2000: 'fog',
    2100: 'fog',
    4000: 'sleet',
    4001: 'rain',
    4200: 'rain',
    4201: 'rain',
    5000: 'snow',
    5001: 'snow',
    5100: 'snow',
    5101: 'snow',
    6000: 'sleet',
    6001: 'sleet',
    6200: 'sleet',
    6201: 'sleet',
    7000: 'sleet',
    7101: 'sleet',
    7102: 'sleet',
    8000: 'thunderstorm'
}


def wxfinished_tm_current():
    """Get current weather conditions from Tomorrow.io"""
    wxstr = str(wxreply.readAll(), 'utf-8')

    try:
        wxdata = json.loads(wxstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.tomorrow.io: ' + wxstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'message' in wxdata:
        print('ERROR: Response from api.tomorrow.io: ' + str(wxdata['code']) + ' - ' + str(wxdata['type']) + ' - ' +
              str(wxdata['message']))
        return

    f = wxdata['data']['timelines'][0]['intervals'][0]
    dt = dateutil.parser.parse(f['startTime']).astimezone(tzlocal.get_localzone())
    icon = f['values']['weatherCode']
    icon = tm_code_icons[icon]
    if not daytime:
        icon = icon.replace('-day', '-night')
    wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, icon + '.png'))
    wxicon.setPixmap(wxiconpixmap.scaled(
        wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxicon2.setPixmap(wxiconpixmap.scaled(
        wxicon.width(),
        wxicon.height(),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxdesc.setText(tm_code_map[f['values']['weatherCode']])
    wxdesc2.setText(tm_code_map[f['values']['weatherCode']])

    wd = ''
    if 'windDirection' in f['values']:
        if Config.wind_degrees:
            wd = str(f['values']['windDirection']) + u'° '
        else:
            wd = bearing(f['values']['windDirection']) + ' '

    gust = ''
    if 'windGust' in f['values']:
        if Config.metric:
            gust = (Config.Lgusting +
                    '%.1f' % (mph2kph(f['values']['windGust'])) + 'km/h')
        else:
            gust = (Config.Lgusting +
                    '%.1f' % (f['values']['windGust']) + 'mph')

    if Config.metric:
        temper.setText('%.1f' % (tempf2tempc(f['values']['temperature'])) + u'°C')
        temper2.setText('%.1f' % (tempf2tempc(f['values']['temperature'])) + u'°C')
        wind.setText(Config.LWind + wd +
                     '%.1f' % (mph2kph(f['values']['windSpeed'])) + 'km/h' +
                     gust)
        feelslike.setText(Config.LFeelslike +
                          '%.1f' % (tempf2tempc(f['values']['temperatureApparent'])) + u'°C')
    else:
        temper.setText('%.1f' % (f['values']['temperature']) + u'°F')
        temper2.setText('%.1f' % (f['values']['temperature']) + u'°F')
        wind.setText(Config.LWind + wd +
                     '%.1f' % (f['values']['windSpeed']) + 'mph' +
                     gust)
        feelslike.setText(Config.LFeelslike +
                          '%.1f' % (f['values']['temperatureApparent']) + u'°F')

    if Config.pressure_mbar:
        press.setText(Config.LPressure + '%.1f' % inhg2mbar(f['values']['pressureSeaLevel']) + 'mbar')
    else:
        press.setText(Config.LPressure + '%.2f' % (f['values']['pressureSeaLevel']) + 'inHg')

    humidity.setText(Config.LHumidity + '%.0f%%' % (f['values']['humidity']))
    wdate.setText('{0:%H:%M %Z}'.format(dt))


def wxfinished_tm_hourly():
    """Get the hourly weather forecast from Tomorrow.io"""
    attribution.setText('Tomorrow.io')
    attribution2.setText('Tomorrow.io')

    wxstr2 = str(wxreply2.readAll(), 'utf-8')

    try:
        wxdata2 = json.loads(wxstr2)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.tomorrow.io: ' + wxstr2)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'message' in wxdata2:
        print('ERROR: Response from api.tomorrow.io: ' + str(wxdata2['code']) + ' - ' + wxdata2['type'] + ' - ' +
              wxdata2['message'])
        return

    for i in range(0, 3):
        f = wxdata2['data']['timelines'][0]['intervals'][i * 3 + 2]
        fl = forecast[i]
        wicon = f['values']['weatherCode']
        wicon = tm_code_icons[wicon]

        dt = dateutil.parser.parse(f['startTime']).astimezone(tzlocal.get_localzone())
        if dt.day == datetime.datetime.now().day:
            fdaytime = daytime
        else:
            fsunrise = sun.sunrise(dt)
            fsunset = sun.sunset(dt)
            if fsunrise <= dt <= fsunset:
                fdaytime = True
            else:
                fdaytime = False

        if not fdaytime:
            wicon = wicon.replace('-day', '-night')
        icon = fl.findChild(QtWidgets.QLabel, 'icon')
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(),
            icon.height(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, 'wx')
        day = fl.findChild(QtWidgets.QLabel, 'day')
        day.setText('{0:%a %I:%M%p}'.format(dt))
        s = ''
        pop = float(f['values']['precipitationProbability'])
        ptype = f['values']['precipitationType']
        if ptype == 0:
            ptype = ''
        paccum = f['values']['precipitationIntensity']

        if pop > 0.0 or ptype != '':
            s += '%.0f' % pop + '% '
        if Config.metric:
            if ptype == 2:
                if paccum > 0.1:
                    s += Config.LSnow + '%.1f' % inches2mm(paccum) + 'mm/hr '
            else:
                if paccum > 0.1:
                    s += Config.LRain + '%.1f' % inches2mm(paccum) + 'mm/hr '
            s += '%.0f' % tempf2tempc(f['values']['temperature']) + u'°C'
        else:
            if ptype == 2:
                if paccum > 0.1:
                    s += Config.LSnow + '%.1f' % paccum + 'in/hr '
            else:
                if paccum > 0.1:
                    s += Config.LRain + '%.1f' % paccum + 'in/hr '
            s += '%.0f' % (f['values']['temperature']) + u'°F'

        wx.setStyleSheet('#wx { font-size: ' + str(int(19 * xscale * Config.fontmult)) + 'px; }')
        wx.setText(tm_code_map[f['values']['weatherCode']] + '\n' + s)


def wxfinished_tm_daily():
    """Get the daily weather forecast from Tomorrow.io"""
    wxstr3 = str(wxreply3.readAll(), 'utf-8')

    try:
        wxdata3 = json.loads(wxstr3)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.tomorrow.io: ' + wxstr3)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'message' in wxdata3:
        print('ERROR: Response from api.tomorrow.io: ' + str(wxdata3['code']) + ' - ' + wxdata3['type'] + ' - ' +
              wxdata3['message'])
        return

    dt = dateutil.parser.parse(wxdata3['data']['timelines'][0]['startTime']).astimezone(tzlocal.get_localzone())
    ioff = 0
    if datetime.datetime.now().day != dt.day:
        ioff += 1
    for i in range(3, 9):
        try:
            f = wxdata3['data']['timelines'][0]['intervals'][i - 3 + ioff]
            wicon = f['values']['weatherCode']
            wicon = tm_code_icons[wicon]
            fl = forecast[i]
            icon = fl.findChild(QtWidgets.QLabel, 'icon')
            wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, wicon + '.png'))
            icon.setPixmap(wxiconpixmap.scaled(
                icon.width(),
                icon.height(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation))
            wx = fl.findChild(QtWidgets.QLabel, 'wx')
            day = fl.findChild(QtWidgets.QLabel, 'day')
            day.setText('{0:%a %m/%d}'.format(dateutil.parser.parse(f['startTime'])
                                              .astimezone(tzlocal.get_localzone())))
            s = ''
            pop = float(f['values']['precipitationProbability'])
            ptype = ''
            paccum = float(f['values']['precipitationIntensity'])
            wc = tm_code_icons[f['values']['weatherCode']]

            if '4000' in wc:
                ptype = 'rain'
            if '4001' in wc:
                ptype = 'rain'
            if '4200' in wc:
                ptype = 'rain'
            if '4201' in wc:
                ptype = 'rain'
            if '5000' in wc:
                ptype = 'snow'
            if '5001' in wc:
                ptype = 'snow'
            if '5100' in wc:
                ptype = 'snow'
            if '5101' in wc:
                ptype = 'snow'
            if '6000' in wc:
                ptype = 'rain'
            if '6001' in wc:
                ptype = 'rain'
            if '6200' in wc:
                ptype = 'rain'
            if '6201' in wc:
                ptype = 'rain'
            if '7000' in wc:
                ptype = 'snow'
            if '7101' in wc:
                ptype = 'snow'
            if '7102' in wc:
                ptype = 'snow'
            if '8000' in wc:
                ptype = 'rain'

            if pop > 0.05 or ptype != '':
                s += '%.0f' % pop + '% '
            if Config.metric:
                if ptype == 'snow':
                    if paccum > 0.1:
                        s += Config.LSnow + '%.1f' % inches2mm(paccum) + 'mm/hr '
                else:
                    if paccum > 0.1:
                        s += Config.LRain + '%.1f' % inches2mm(paccum) + 'mm/hr '
                s += '%.0f' % tempf2tempc(f['values']['temperatureMax']) + '/' + \
                     '%.0f' % tempf2tempc(f['values']['temperatureMin']) + u'°C'
            else:
                if ptype == 'snow':
                    if paccum > 0.1:
                        s += Config.LSnow + '%.1f' % paccum + 'in/hr '
                else:
                    if paccum > 0.1:
                        s += Config.LRain + '%.1f' % paccum + 'in/hr '
                s += '%.0f' % f['values']['temperatureMax'] + '/' + \
                     '%.0f' % f['values']['temperatureMin'] + u'°F'

            wx.setStyleSheet('#wx { font-size: ' + str(int(19 * xscale * Config.fontmult)) + 'px; }')
            wx.setText(tm_code_map[f['values']['weatherCode']] + '\n' + s)
        except IndexError:
            print('WARNING:', traceback.format_exc())
            pass


def getwx_tm():
    """Get weather from Tomorrow.io"""
    global wxreply, wxreply2, wxreply3

    if not hasattr(ApiKeys, 'tmapi'):
        return

    tmapi = ApiKeys.tmapi

    if not hasMetar:
        # current conditions
        wxurl = 'https://api.tomorrow.io/v4/timelines?timesteps=current&apikey=' + tmapi
        wxurl += '&location=' + str(Config.location.lat) + ',' + str(Config.location.lng)
        wxurl += '&units=imperial'
        wxurl += '&fields=temperature,weatherCode,temperatureApparent,humidity,'
        wxurl += 'windSpeed,windDirection,windGust,pressureSeaLevel,precipitationType'
        print('INFO: getting Tomorrow.io current conditions: ' + safeurl(wxurl))
        r = QUrl(wxurl)
        r = QNetworkRequest(r)
        wxreply = manager.get(r)
        wxreply.finished.connect(wxfinished_tm_current)

    # hourly forecast
    wxurl2 = 'https://api.tomorrow.io/v4/timelines?timesteps=1h&apikey=' + tmapi
    wxurl2 += '&location=' + str(Config.location.lat) + ',' + str(Config.location.lng)
    wxurl2 += '&units=imperial'
    wxurl2 += '&fields=temperature,precipitationIntensity,precipitationType,'
    wxurl2 += 'precipitationProbability,weatherCode'
    print('INFO: getting Tomorrow.io hourly forecast: ' + safeurl(wxurl2))
    r2 = QUrl(wxurl2)
    r2 = QNetworkRequest(r2)
    wxreply2 = manager.get(r2)
    wxreply2.finished.connect(wxfinished_tm_hourly)

    # daily forecast
    wxurl3 = 'https://api.tomorrow.io/v4/timelines?timesteps=1d&apikey=' + tmapi
    wxurl3 += '&location=' + str(Config.location.lat) + ',' + str(Config.location.lng)
    wxurl3 += '&units=imperial'
    wxurl3 += '&fields=temperature,precipitationIntensity,precipitationType,'
    wxurl3 += 'precipitationProbability,weatherCode,temperatureMax,temperatureMin'
    print('INFO: getting Tomorrow.io daily forecast: ' + safeurl(wxurl3))
    r3 = QUrl(wxurl3)
    r3 = QNetworkRequest(r3)
    wxreply3 = manager.get(r3)
    wxreply3.finished.connect(wxfinished_tm_daily)


metar_cond = [
    ('CLR', '', '', 'Clear', 'clear-day', 0),
    ('NSC', '', '', 'Clear', 'clear-day', 0),
    ('SKC', '', '', 'Clear', 'clear-day', 0),
    ('FEW', '', '', 'Few Clouds', 'partly-cloudy-day', 1),
    ('NCD', '', '', 'Clear', 'clear-day', 0),
    ('SCT', '', '', 'Scattered Clouds', 'partly-cloudy-day', 2),
    ('BKN', '', '', 'Mostly Cloudy', 'partly-cloudy-day', 3),
    ('OVC', '', '', 'Cloudy', 'cloudy', 4),

    ('///', '', '', '', 'cloudy', 0),
    ('UP', '', '', '', 'cloudy', 0),
    ('VV', '', '', '', 'cloudy', 0),
    ('//', '', '', '', 'cloudy', 0),

    ('DZ', '', '', 'Drizzle', 'rain', 10),

    ('RA', 'FZ', '+', 'Heavy Freezing Rain', 'sleet', 11),
    ('RA', 'FZ', '-', 'Light Freezing Rain', 'sleet', 11),
    ('RA', 'SH', '+', 'Heavy Rain Showers', 'sleet', 11),
    ('RA', 'SH', '-', 'Light Rain Showers', 'rain', 11),
    ('RA', 'BL', '+', 'Heavy Blowing Rain', 'rain', 11),
    ('RA', 'BL', '-', 'Light Blowing Rain', 'rain', 11),
    ('RA', 'FZ', '', 'Freezing Rain', 'sleet', 11),
    ('RA', 'SH', '', 'Rain Showers', 'rain', 11),
    ('RA', 'BL', '', 'Blowing Rain', 'rain', 11),
    ('RA', '', '+', 'Heavy Rain', 'rain', 11),
    ('RA', '', '-', 'Light Rain', 'rain', 11),
    ('RA', '', '', 'Rain', 'rain', 11),

    ('SN', 'FZ', '+', 'Heavy Freezing Snow', 'snow', 12),
    ('SN', 'FZ', '-', 'Light Freezing Snow', 'snow', 12),
    ('SN', 'SH', '+', 'Heavy Snow Showers', 'snow', 12),
    ('SN', 'SH', '-', 'Light Snow Showers', 'snow', 12),
    ('SN', 'BL', '+', 'Heavy Blowing Snow', 'snow', 12),
    ('SN', 'BL', '-', 'Light Blowing Snow', 'snow', 12),
    ('SN', 'FZ', '', 'Freezing Snow', 'snow', 12),
    ('SN', 'SH', '', 'Snow Showers', 'snow', 12),
    ('SN', 'BL', '', 'Blowing Snow', 'snow', 12),
    ('SN', '', '+', 'Heavy Snow', 'snow', 12),
    ('SN', '', '-', 'Light Snow', 'snow', 12),
    ('SN', '', '', 'Snow', 'snow', 12),

    ('SG', 'BL', '', 'Blowing Snow', 'snow', 12),
    ('SG', '', '', 'Snow', 'snow', 12),
    ('GS', 'BL', '', 'Blowing Snow Pellets', 'snow', 12),
    ('GS', '', '', 'Snow Pellets', 'snow', 12),

    ('IC', '', '', 'Ice Crystals', 'snow', 13),
    ('PL', '', '', 'Ice Pellets', 'snow', 13),

    ('GR', '', '+', 'Heavy Hail', 'thunderstorm', 14),
    ('GR', '', '', 'Hail', 'thunderstorm', 14),
]


def feels_like(f):
    t = f.temp.value('C')
    d = f.dewpt.value('C')
    h = (math.exp((17.625 * d) / (243.04 + d)) /
         math.exp((17.625 * t) / (243.04 + t)))
    t = f.temp.value('F')
    w = 0
    if f.wind_speed:
        w = f.wind_speed.value('MPH')
    if t > 80 and h >= 0.40:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * h - .22475541 * t * h -
              .00683783 * t * t - .05481717 * h * h + .00122874 * t * t * h +
              .00085282 * t * h * h - .00000199 * t * t * h * h)
        if h < 0.13:
            if 80.0 <= t <= 112.0:
                hi -= ((13 - h) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        if h > 0.85:
            if 80.0 <= t <= 112.0:
                hi += ((h - 85) / 10) * ((87 - t) / 5)
        return hi
    if t < 50 and w >= 3:
        wc = 35.74 + 0.6215 * t - 35.75 * \
             (w ** 0.16) + 0.4275 * t * (w ** 0.16)
        return wc
    return t


def wxfinished_metar():
    """Get current weather conditions from NOAA METAR"""
    wxstr = str(metarreply.readAll(), 'utf-8')

    if metarreply.error() != QNetworkReply.NoError:
        print('ERROR: Response from nws.noaa.gov: ' + wxstr)
        return

    for wxline in wxstr.splitlines():
        if wxline.startswith(Config.METAR):
            wxstr = wxline
    print('INFO: wxmetar: ' + wxstr)
    f = Metar.Metar(wxstr, strict=False)
    dt = datetime.time(0, 0, 0, tzinfo=datetime.timezone.utc)
    if f.time:
        dt = f.time.replace(tzinfo=datetime.timezone.utc).astimezone(tzlocal.get_localzone())

    pri = -1
    weather = ''
    icon = ''
    if f.sky:
        for s in f.sky:
            for c in metar_cond:
                if s[0] == c[0]:
                    if c[5] > pri:
                        pri = c[5]
                        weather = c[3]
                        icon = c[4]
    # A report lists present weather in decreasing significance, so the first
    # group that matches is the one to show.  wpri ranks the table rows that
    # match this one group, which is what picks Light Rain over the plainer
    # Rain; it does not carry across groups, or a later and less significant
    # group could outrank an earlier one.
    for w in f.weather:
        wpri = -1
        for c in metar_cond:
            if w[2] == c[0]:
                if c[1] > '':
                    if w[1] == c[1]:
                        if c[2] > '':
                            if w[0][0:1] == c[2]:
                                if c[5] > wpri:
                                    wpri = c[5]
                                    weather = c[3]
                                    icon = c[4]
                else:
                    if c[2] > '':
                        if w[0][0:1] == c[2]:
                            if c[5] > wpri:
                                wpri = c[5]
                                weather = c[3]
                                icon = c[4]
                    else:
                        if c[5] > wpri:
                            wpri = c[5]
                            weather = c[3]
                            icon = c[4]
        if wpri > -1:
            break

    if not daytime:
        icon = icon.replace('-day', '-night')

    wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, icon + '.png'))
    wxicon.setPixmap(wxiconpixmap.scaled(
        wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxicon2.setPixmap(wxiconpixmap.scaled(
        wxicon.width(),
        wxicon.height(),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation))
    wxdesc.setText(weather)
    wxdesc2.setText(weather)

    temp_str = ''
    pressure_str = Config.LPressure
    humidity_str = Config.LHumidity
    wind_speed_str = Config.LWind
    wind_dir_str = ''
    feelslike_str = Config.LFeelslike

    if f.wind_dir:
        if Config.wind_degrees:
            wind_dir_str = str(f.wind_dir.value()) + u'°'
        else:
            wind_dir_str = f.wind_dir.compass()

    if Config.metric:
        if f.temp:
            temp_str = '%.1f' % f.temp.value('C')
        temp_str += u'°C'
        if f.wind_speed:
            wind_speed_str += wind_dir_str + ' ' + '%.1f' % f.wind_speed.value('KMH') + 'km/h'
            if f.wind_gust:
                wind_speed_str += Config.Lgusting + '%.1f' % f.wind_gust.value('KMH') + 'km/h'
        if f.temp and f.dewpt:
            feelslike_str += '%.1f' % tempf2tempc(feels_like(f)) + u'°C'
    else:
        if f.temp:
            temp_str = '%.1f' % f.temp.value('F')
        temp_str += u'°F'
        if f.wind_speed:
            wind_speed_str += wind_dir_str + ' ' + '%.1f' % f.wind_speed.value('MPH') + 'mph'
            if f.wind_gust:
                wind_speed_str += Config.Lgusting + '%.1f' % f.wind_gust.value('MPH') + 'mph'
        if f.temp and f.dewpt:
            feelslike_str += '%.1f' % feels_like(f) + u'°F'

    if f.press:
        if Config.pressure_mbar:
            pressure_str += '%.1f' % f.press.value('MB') + 'mbar'
        else:
            pressure_str += '%.2f' % f.press.value('IN') + 'inHg'

    if f.temp and f.dewpt:
        t = f.temp.value('C')
        d = f.dewpt.value('C')
        h = 100.0 * (math.exp((17.625 * d) / (243.04 + d)) /
                     math.exp((17.625 * t) / (243.04 + t)))
        humidity_str += '%.0f%%' % h

    temper.setText(temp_str)
    temper2.setText(temp_str)
    press.setText(pressure_str)
    humidity.setText(humidity_str)
    wind.setText(wind_speed_str)
    feelslike.setText(feelslike_str)
    wdate.setText('{0:%H:%M %Z} {1}'.format(dt, Config.METAR))


def getwx_metar():
    """Get current weather conditions from NOAA METAR"""
    global metarreply
    metarurl = 'https://tgftp.nws.noaa.gov/data/observations/metar/stations/' + Config.METAR + '.TXT'
    print('INFO: getting METAR current conditions: ' + metarurl)
    r = QUrl(metarurl)
    r = QNetworkRequest(r)
    metarreply = manager.get(r)
    metarreply.finished.connect(wxfinished_metar)


om_code_icons = {
    0: 'clear-day',
    1: 'clear-day',
    2: 'partly-cloudy-day',
    3: 'cloudy',
    45: 'fog',
    48: 'fog',
    51: 'rain',
    53: 'rain',
    55: 'rain',
    56: 'sleet',
    57: 'sleet',
    61: 'rain',
    63: 'rain',
    65: 'rain',
    66: 'sleet',
    67: 'sleet',
    71: 'snow',
    73: 'snow',
    75: 'snow',
    77: 'snow',
    80: 'rain',
    81: 'rain',
    82: 'rain',
    85: 'snow',
    86: 'snow',
    95: 'thunderstorm',
    96: 'thunderstorm',
    99: 'thunderstorm'
}

om_code_map = {
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

om_snow_codes = (71, 73, 75, 77, 85, 86)


def om_icon(code, isday):
    icon = om_code_icons.get(code, 'cloudy')
    if not isday:
        icon = icon.replace('-day', '-night')
    return icon


def wxfinished_om():
    """Get current weather conditions and forecast from Open-Meteo.com"""
    attribution.setText('Open-Meteo.com')
    attribution2.setText('Open-Meteo.com')

    wxstr = str(wxreply.readAll(), 'utf-8')

    try:
        wxdata = json.loads(wxstr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from api.open-meteo.com: ' + wxstr)
        print('WARNING: Moving on...')
        return  # ignore and try again on the next refresh

    if 'error' in wxdata:
        print('ERROR: Response from api.open-meteo.com: ' + str(wxdata.get('reason')))
        return

    if not hasMetar:
        c = wxdata['current']
        dt = dateutil.parser.parse(c['time']).astimezone(tzlocal.get_localzone())
        icon = om_icon(c['weather_code'], c['is_day'])
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons, icon + '.png'))
        wxicon.setPixmap(wxiconpixmap.scaled(
            wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wxicon2.setPixmap(wxiconpixmap.scaled(
            wxicon.width(), wxicon.height(), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wxdesc.setText(om_code_map.get(c['weather_code'], ''))
        wxdesc2.setText(om_code_map.get(c['weather_code'], ''))

        wd = ''
        if c.get('wind_direction_10m') is not None:
            if Config.wind_degrees:
                wd = str(c['wind_direction_10m']) + u'° '
            else:
                wd = bearing(c['wind_direction_10m']) + ' '

        gust = ''
        if c.get('wind_gusts_10m') is not None:
            if Config.metric:
                gust = (Config.Lgusting +
                        '%.1f' % (mph2kph(c['wind_gusts_10m'])) + 'km/h')
            else:
                gust = (Config.Lgusting +
                        '%.1f' % (c['wind_gusts_10m']) + 'mph')

        if Config.metric:
            temper.setText('%.1f' % (tempf2tempc(c['temperature_2m'])) + u'°C')
            temper2.setText('%.1f' % (tempf2tempc(c['temperature_2m'])) + u'°C')
            wind.setText(Config.LWind + wd +
                         '%.1f' % (mph2kph(c['wind_speed_10m'])) + 'km/h' +
                         gust)
            feelslike.setText(Config.LFeelslike +
                              '%.1f' % (tempf2tempc(c['apparent_temperature'])) + u'°C')
        else:
            temper.setText('%.1f' % (c['temperature_2m']) + u'°F')
            temper2.setText('%.1f' % (c['temperature_2m']) + u'°F')
            wind.setText(Config.LWind + wd +
                         '%.1f' % (c['wind_speed_10m']) + 'mph' +
                         gust)
            feelslike.setText(Config.LFeelslike +
                              '%.1f' % (c['apparent_temperature']) + u'°F')

        if Config.pressure_mbar:
            press.setText(Config.LPressure + '%.1f' % c['pressure_msl'] + 'mbar')
        else:
            press.setText(Config.LPressure + '%.2f' % mbar2inhg(c['pressure_msl']) + 'inHg')

        humidity.setText(Config.LHumidity +
                         '%.0f%%' % (c['relative_humidity_2m']))
        wdate.setText('{0:%H:%M %Z}'.format(dt))

    h = wxdata['hourly']
    now = datetime.datetime.now()
    base = 0
    for i in range(0, len(h['time'])):
        if dateutil.parser.parse(h['time'][i]) > now:
            base = i
            break

    for i in range(0, 3):
        j = base + i * 3 + 2
        if j >= len(h['time']):
            break
        fl = forecast[i]
        ht = dateutil.parser.parse(h['time'][j]).astimezone(tzlocal.get_localzone())
        if ht.day == now.day:
            fdaytime = daytime
        else:
            fsunrise = sun.sunrise(ht)
            fsunset = sun.sunset(ht)
            fdaytime = fsunrise <= ht <= fsunset
        code = h['weather_code'][j]
        icon = fl.findChild(QtWidgets.QLabel, 'icon')
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons,
                                                  om_icon(code, fdaytime) + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(), icon.height(), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, 'wx')
        day = fl.findChild(QtWidgets.QLabel, 'day')
        day.setText('{0:%a %I:%M%p}'.format(ht))
        s = ''
        pop = h['precipitation_probability'][j]
        paccum = h['precipitation'][j]
        if pop > 0:
            s += '%.0f' % pop + '% '
        if paccum > 0.01:
            if code in om_snow_codes:
                s += Config.LSnow
            else:
                s += Config.LRain
            if Config.metric:
                s += '%.0f' % inches2mm(paccum) + 'mm/hr '
            else:
                s += '%.2f' % paccum + 'in/hr '
        if Config.metric:
            s += '%.0f' % tempf2tempc(h['temperature_2m'][j]) + u'°C'
        else:
            s += '%.0f' % h['temperature_2m'][j] + u'°F'
        wx.setText(om_code_map.get(code, '') + '\n' + s)

    d = wxdata['daily']
    for i in range(3, 9):
        j = i - 3
        if j >= len(d['time']):
            break
        fl = forecast[i]
        code = d['weather_code'][j]
        icon = fl.findChild(QtWidgets.QLabel, 'icon')
        wxiconpixmap = QtGui.QPixmap(os.path.join(Config.icons,
                                                  om_icon(code, True) + '.png'))
        icon.setPixmap(wxiconpixmap.scaled(
            icon.width(), icon.height(), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation))
        wx = fl.findChild(QtWidgets.QLabel, 'wx')
        day = fl.findChild(QtWidgets.QLabel, 'day')
        day.setText('{0:%a %m/%d}'.format(dateutil.parser.parse(d['time'][j])))
        s = ''
        pop = d['precipitation_probability_max'][j]
        paccum = d['precipitation_sum'][j]
        if pop > 0:
            s += '%.0f' % pop + '% '
        if paccum > 0.01:
            if code in om_snow_codes:
                s += Config.LSnow
            else:
                s += Config.LRain
            if Config.metric:
                s += '%.0f' % inches2mm(paccum) + 'mm '
            else:
                s += '%.2f' % paccum + 'in '
        if Config.metric:
            s += ('%.0f' % tempf2tempc(d['temperature_2m_max'][j]) + '/' +
                  '%.0f' % tempf2tempc(d['temperature_2m_min'][j])) + u'°C'
        else:
            s += ('%.0f' % d['temperature_2m_max'][j] + '/' +
                  '%.0f' % d['temperature_2m_min'][j]) + u'°F'
        wx.setText(om_code_map.get(code, '') + '\n' + s)


def getwx_om():
    """Get weather from Open-Meteo.com"""
    global wxreply
    wxurl = ('https://api.open-meteo.com/v1/forecast?latitude=' +
             str(Config.location.lat) +
             '&longitude=' + str(Config.location.lng))
    wxurl += '&current=temperature_2m,relative_humidity_2m,'
    wxurl += 'apparent_temperature,is_day,weather_code,pressure_msl,'
    wxurl += 'wind_speed_10m,wind_direction_10m,wind_gusts_10m'
    wxurl += '&hourly=temperature_2m,weather_code,'
    wxurl += 'precipitation_probability,precipitation'
    wxurl += '&daily=weather_code,temperature_2m_max,temperature_2m_min,'
    wxurl += 'precipitation_sum,precipitation_probability_max'
    wxurl += '&temperature_unit=fahrenheit&wind_speed_unit=mph'
    wxurl += '&precipitation_unit=inch&timezone=auto&forecast_days=8'
    wxurl += '&r=' + str(random.random())
    print('INFO: getting Open-Meteo current conditions and forecast: ' + wxurl)
    r = QUrl(wxurl)
    r = QNetworkRequest(r)
    wxreply = manager.get(r)
    wxreply.finished.connect(wxfinished_om)


def getwx():
    """Get weather from a selected provider"""
    global om_code_map, tm_code_map

    # Get weather from NOAA METAR if METAR station code is set in Config file
    if hasMetar:
        try:
            getwx_metar()
        except AttributeError:
            pass

    # Get weather from Open-Meteo.com if useopenmeteo = 1 in Config file
    if getattr(Config, 'useopenmeteo', False):
        # Use language-specific terms for Open-Meteo.com weather conditions if set in Config file
        # else use default terms
        om_code_map = getattr(Config, 'Lom_code_map', om_code_map)
        getwx_om()
        return

    # Get weather from Tomorrow.io if tmapi key is set in ApiKeys file
    if hasattr(ApiKeys, 'tmapi'):
        # Use language-specific terms for Tomorrow.io weather conditions if set in Config file
        # else use default terms
        tm_code_map = getattr(Config, 'Ltm_code_map', tm_code_map)
        getwx_tm()
        return

    # Get weather from OpenWeatherMap.org if owmapi key is set in ApiKeys file
    if hasattr(ApiKeys, 'owmapi'):
        getwx_owm()
        return

    # Fallback: Get weather from Open-Meteo.com
    om_code_map = getattr(Config, 'Lom_code_map', om_code_map)
    getwx_om()


def qtstart():
    """Start the Qt application"""
    global ctimer, wxtimer, temptimer, metadatatimer
    global sun, daytime, sunrise, sunset
    global tzlatlng

    if Config.DateLocale != '':
        try:
            locale.setlocale(locale.LC_TIME, Config.DateLocale)
        except locale.Error:
            print('WARNING:', traceback.format_exc())
            pass

    dt = datetime.datetime.now(tz=tzlocal.get_localzone())
    tzlatlngstr = get_tz(Config.location.lng, Config.location.lat)
    if tzlatlngstr:
        tzlatlng = pytz.timezone(tzlatlngstr)
    else:
        tzlatlng = tzlocal.get_localzone()
        print(
            "WARNING: tzfpy.get_tz() returned None for lat/lng "
            f"({Config.location.lat}, {Config.location.lng}); "
            f"falling back to tzlocal.get_localzone() -> {tzlatlng}"
        )

    sun = SunTimes(Config.location.lat, Config.location.lng, tzlatlng)
    sunrise = sun.sunrise(dt)
    sunset = sun.sunset(dt)
    if sunrise <= dt <= sunset:
        daytime = True
    else:
        daytime = False

    getwx()

    gettemp()

    objradar1.start(Config.radar_refresh * 60)
    objradar2.start(Config.radar_refresh * 60)
    objradar3.start(Config.radar_refresh * 60)
    objradar4.start(Config.radar_refresh * 60)

    # Only start wxstart() for radars on the visible startup screen (frame)
    if Config.startup_screen == 2:
        objradar3.wxstart()
        objradar4.wxstart()
    else:
        objradar1.wxstart()
        objradar2.wxstart()

    ctimer = QtCore.QTimer()
    ctimer.timeout.connect(tick)
    ctimer.start(1000)

    wxtimer = QtCore.QTimer()
    wxtimer.timeout.connect(getwx)
    wxtimer.start(int(1000 * Config.weather_refresh * 60 + random.uniform(1000, 10000)))

    temptimer = QtCore.QTimer()
    temptimer.timeout.connect(gettemp)
    temptimer.start(int(1000 * 10 * 60 + random.uniform(1000, 10000)))

    # Fetch weather radar metadata once at regular intervals (every 10 minutes)
    metadatatimer = QtCore.QTimer()
    metadatatimer.timeout.connect(get_wx_radar_metadata)
    metadatatimer.start(int(1000 * 600 + random.uniform(1000, 5000)))  # 10 minutes

    # Fetch metadata immediately on startup
    get_wx_radar_metadata(force=True)

    if Config.useslideshow:
        objimage1.start(Config.slide_time)


class SlideShow(QtWidgets.QLabel):
    def __init__(self, parent, rect, myname):
        self.myname = myname
        self.rect = rect
        super().__init__(parent)

        self.pause = False
        self.count = 0
        self.img_list = []
        self.img_inc = 1

        self.get_images()

        self.setObjectName('slideShow')
        self.setGeometry(rect)
        self.setStyleSheet('#slideShow { background-color: ' +
                           Config.slide_bg_color + '; }')
        self.setAlignment(Qt.AlignHCenter | Qt.AlignCenter)

        self.timer = None

    def start(self, interval):
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.run_ss)
        self.timer.start(int(1000 * interval + random.uniform(1, 10)))
        self.run_ss()

    def stop(self):
        try:
            self.timer.stop()
            self.timer = None
        except AttributeError:
            print('WARNING:', traceback.format_exc())
            pass

    def run_ss(self):
        self.get_images()
        self.switch_image()

    def switch_image(self):
        if self.img_list:
            if not self.pause:
                self.count += self.img_inc
                if self.count >= len(self.img_list):
                    self.count = 0
                self.show_image(self.img_list[self.count])
                self.img_inc = 1

    def show_image(self, image):
        image = QtGui.QImage(image)

        bg = QtGui.QPixmap.fromImage(image)
        self.setPixmap(bg.scaled(
            self.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))

    def get_images(self):
        self.get_local(Config.slides)

    def play_pause(self):
        if not self.pause:
            self.pause = True
        else:
            self.pause = False

    def prev_next(self, direction):
        self.img_inc = direction
        self.timer.stop()
        self.switch_image()
        self.timer.start()

    def get_local(self, path):
        try:
            dir_content = os.listdir(path)
            for each in dir_content:
                full_file = os.path.join(path, each)
                if os.path.isfile(full_file) and (full_file.lower().endswith('png')
                                                  or full_file.lower().endswith('jpg')):
                    self.img_list.append(full_file)
        except OSError:
            print('ERROR:', traceback.format_exc())


# Global weather radar metadata cache (shared by all Radar instances)
radarMetadataCache = {
    'data': {},
    'host': '',
    'paths': {},
    'provider': '',
    'lastupdated': 0,
    'lastattempt': 0,
    'inprogress': False,
    'updateinterval': 600,  # refresh every 10 minutes (same as tile intervals)
    'retryinterval': 30  # wait at least 30 seconds after a failed attempt
}
radarMetadataReply = None


def get_wx_radar_metadata(force=False):
    """Fetch weather radar metadata once globally, shared by all radar instances."""
    global manager, radarMetadataCache, radarMetadataReply

    now = time.time()

    # Do not start another request while one is already running.
    if radarMetadataCache['inprogress']:
        return

    # Check if the cache is still fresh.
    if not force and now - radarMetadataCache['lastupdated'] < radarMetadataCache['updateinterval']:
        return

    # If the last attempt failed, avoid retrying every 200ms from every active radar.
    if not force and now - radarMetadataCache['lastattempt'] < radarMetadataCache['retryinterval']:
        return

    if Config.userainviewer:
        metadataurl = 'https://api.rainviewer.com/public/weather-maps.json'
        radarMetadataCache['provider'] = 'RainViewer.com'
    else:
        metadataurl = 'https://api.librewxr.net/public/weather-maps.json'
        radarMetadataCache['provider'] = 'LibreWXR.net'

    radarMetadataCache['inprogress'] = True
    radarMetadataCache['lastattempt'] = now

    print('INFO: Fetching weather radar metadata: ' + metadataurl)
    metadatareq = QNetworkRequest(QUrl(metadataurl))
    radarMetadataReply = manager.get(metadatareq)

    reply = radarMetadataReply
    reply.finished.connect(lambda reply=reply: wx_radar_metadata_finished(reply))


def wx_radar_metadata_finished(reply):
    """Process the weather radar metadata response."""
    global radarMetadataCache, radarMetadataReply

    radarMetadataCache['inprogress'] = False

    if reply.error() != QNetworkReply.NoError:
        metadatastr = str(reply.readAll(), 'utf-8')
        print('ERROR: Response from weather radar provider: ' + metadatastr)
        reply.deleteLater()
        if reply is radarMetadataReply:
            radarMetadataReply = None
        return

    metadatastr = str(reply.readAll(), 'utf-8')
    reply.deleteLater()
    if reply is radarMetadataReply:
        radarMetadataReply = None

    if not metadatastr.strip():
        print('WARNING: Empty response from weather radar provider')
        return

    try:
        metadata = json.loads(metadatastr)
    except ValueError:  # includes json.decoder.JSONDecodeError
        print('WARNING:', traceback.format_exc())
        print('WARNING: Response from weather radar provider: ' + metadatastr)
        return

    paths = {}
    radar = metadata.get('radar', {})
    for frame in radar.get('past', []) + radar.get('nowcast', []):
        try:
            paths[int(frame['time'])] = frame['path']
        except (KeyError, TypeError, ValueError):
            print('WARNING:', traceback.format_exc())
            pass

    host = metadata.get('host', '')
    if not host:
        print('WARNING: Weather radar metadata response did not include a host')
        return

    if not paths:
        print('WARNING: Weather radar metadata response did not include any radar frames')
        return

    radarMetadataCache['data'] = metadata
    radarMetadataCache['host'] = host
    radarMetadataCache['paths'] = paths
    radarMetadataCache['lastupdated'] = time.time()

    print('INFO: Weather radar metadata updated from ' +
          radarMetadataCache['provider'] +
          ' with ' + str(len(paths)) + ' frames')


class RadarConfig:
    """Validated radar configuration with defaults for optional settings."""
    DEFAULT_BASEMAP = ''
    DEFAULT_OVERLAY = ''
    DEFAULT_COLOR = 2
    DEFAULT_SMOOTH = 1
    DEFAULT_SNOW = 1
    DEFAULT_MARKERS = ()

    def __init__(self, radar, myname):
        try:
            self.center = radar['center']
            self.zoom = radar['zoom']
        except KeyError as exc:
            raise KeyError(f'Radar config for {myname} is missing required parameter: {exc}') from exc

        self.basemap = radar.get('basemap', self.DEFAULT_BASEMAP)
        self.overlay = radar.get('overlay', self.DEFAULT_OVERLAY)
        self.color = radar.get('color', self.DEFAULT_COLOR)
        self.smooth = radar.get('smooth', self.DEFAULT_SMOOTH)
        self.snow = radar.get('snow', self.DEFAULT_SNOW)
        self.markers = radar.get('markers', self.DEFAULT_MARKERS)
        self.oldcolor = 'oldcolor' in radar

    @staticmethod
    def marker_image_file(marker):
        mkfile = marker.get('image', 'teardrop')
        if os.path.dirname(mkfile) == '':
            mkfile = os.path.join('markers', mkfile)
        if os.path.splitext(mkfile)[1] == '':
            mkfile += '.png'
        return mkfile

    @staticmethod
    def marker_height(marker):
        sizes = {
            'small': 64,
            'mid': 70,
            'tiny': 40
        }
        return sizes.get(marker.get('size'), 80)

class Radar(QtWidgets.QLabel):
    TILE_SIZE = 256
    DEFAULT_ANIMATION_FRAMES = 5

    def __init__(self, parent, radar, rect, myname):
        super().__init__(parent)

        self.myname = myname
        self.rect = rect
        self.radar = RadarConfig(radar, myname)
        self.anim = self.DEFAULT_ANIMATION_FRAMES
        self.zoom = self.radar.zoom
        self.point = self.radar.center

        self._init_urls(rect)
        self._init_refresh_state()
        self._init_tile_geometry()
        self._init_widget()
        self._init_layers()
        self._init_frame_state()
        self._init_request_state()

    def _init_urls(self, rect):
        self.baseurl = self.mapurl(rect, overlayonly=False)
        print(f'INFO: map base url for {self.myname}: {safeurl(self.baseurl)}')

        self.overlayurl = ''
        if usemapbox and self.radar.overlay != '':
            self.overlayurl = self.mapurl(rect, overlayonly=True)
            print(f'INFO: map overlay url for {self.myname}: {safeurl(self.overlayurl)}')

    def _init_refresh_state(self):
        self.interval = Config.radar_refresh * 60
        self.baseTime = 0

    def _init_widget(self):
        self.setObjectName('radar')
        self.setGeometry(self.rect)
        self.setStyleSheet('#radar { background-color: grey; }')
        self.setAlignment(Qt.AlignCenter)

    def _make_layer(self, name):
        layer = QtWidgets.QLabel(self)
        layer.setObjectName(name)
        layer.setStyleSheet(f'#{name} {{ background-color: transparent; }}')
        layer.setGeometry(0, 0, self.rect.width(), self.rect.height())
        return layer

    def _init_layers(self):
        self.wwx = self._make_layer('wx')
        self.overlay = self._make_layer('overlay')
        self.wmk = self._make_layer('mk')
        self.timestamp = self._make_layer('timestamp')

    def _init_tile_geometry(self):
        self.corners = get_corners(
            self.point,
            self.zoom,
            self.rect.width(),
            self.rect.height()
        )
        self.cornerTiles = {
            'NW': get_tile_xy(LatLng(self.corners['N'], self.corners['W']), self.zoom),
            'NE': get_tile_xy(LatLng(self.corners['N'], self.corners['E']), self.zoom),
            'SE': get_tile_xy(LatLng(self.corners['S'], self.corners['E']), self.zoom),
            'SW': get_tile_xy(LatLng(self.corners['S'], self.corners['W']), self.zoom),
        }

        self.tiles = []
        self.tiletails = []
        self.totalWidth = 0
        self.totalHeight = 0
        self.tilesWidth = 0
        self.tilesHeight = 0

        self._build_tile_lists()

    def _build_tile_lists(self):
        color = self.radar.color
        smooth = self.radar.smooth
        snow = self.radar.snow

        for y in range(int(self.cornerTiles['NW']['Y']),
                       int(self.cornerTiles['SW']['Y']) + 1):
            self.totalHeight += self.TILE_SIZE
            self.tilesHeight += 1

            for x in range(int(self.cornerTiles['NW']['X']),
                           int(self.cornerTiles['NE']['X']) + 1):
                self.tiles.append({'X': x, 'Y': y})

                if self.radar.oldcolor:
                    tail = '/256/%d/%d/%d.png?color=%d' % (
                        self.zoom,
                        x,
                        y,
                        color
                    )
                else:
                    tail = '/256/%d/%d/%d/%d/%d_%d.png' % (
                        self.zoom,
                        x,
                        y,
                        color,
                        smooth,
                        snow
                    )

                self.tiletails.append(tail)

        for x in range(int(self.cornerTiles['NW']['X']),
                       int(self.cornerTiles['NE']['X']) + 1):
            self.totalWidth += self.TILE_SIZE
            self.tilesWidth += 1

    def _init_frame_state(self):
        self.frameImages = []
        self.displayedFrame = 0
        self.ticker = 0
        self.lastget = 0

    def _init_request_state(self):
        self.getTime = 0
        self.getIndex = 0
        self.tileurls = []
        self.tileQimages = []
        self.tilereply = None
        self.basereply = None
        self.timer = None
        self.overlayreply = None

    def rtick(self):
        """Update radar display at regular intervals"""
        if time.time() > (radarMetadataCache.get('lastupdated', 0) +
                          radarMetadataCache.get('updateinterval', 600)):
            get_wx_radar_metadata()

        if time.time() > (self.lastget + self.interval):
            self.get(int(time.time()))
            self.lastget = time.time()
        if len(self.frameImages) < 1:
            return
        if self.displayedFrame == 0:
            self.ticker += 1
            if self.ticker < 5:
                return
        self.ticker = 0
        if self.displayedFrame < 0 or self.displayedFrame >= len(self.frameImages):
            self.displayedFrame = 0
        f = self.frameImages[self.displayedFrame]
        self.wwx.setPixmap(f['image'])
        self.timestamp.setPixmap(f['timestamp'])
        self.displayedFrame += 1
        if self.displayedFrame >= len(self.frameImages):
            self.displayedFrame = 0

    def get(self, t=0):
        """Retrieve radar tiles for a specific time or the current base time."""
        t = int(t / 600) * 600
        if t > 0:
            if self.baseTime == t:
                return
        if t == 0:
            t = self.baseTime
        else:
            self.baseTime = t
        newf = []
        for f in self.frameImages:
            if f['time'] >= (t - self.anim * 600):
                newf.append(f)
        self.frameImages = newf
        firstt = t - self.anim * 600
        for tt in range(firstt, t + 1, 600):
            print(f'INFO: {self.myname} fetching radar tiles for time {tt} '
                  f'({datetime.datetime.fromtimestamp(tt).astimezone(tzlocal.get_localzone())})')
            gotit = False
            for f in self.frameImages:
                if f['time'] == tt:
                    gotit = True
            if not gotit:
                # Try to get tiles for this time, but continue to the next time if unavailable
                if self.get_tiles(tt):
                    break  # Successfully started fetching, stop loop to wait for async completion

    def _init_request_state(self):
        self.getTime = 0
        self.getIndex = 0
        self.tileurls = []
        self.tileQimages = []
        self.tilereply = None
        self.basereply = None
        self.timer = None
        self.overlayreply = None
        self.tileRetryCount = 0

    def get_tiles(self, t, i=0):
        """Build tile URLs from metadata and fetch them

        Returns True if tiles were successfully queued for fetching, False if unavailable
        """
        t = int(t / 600) * 600
        self.getTime = t
        self.getIndex = i

        if i == 0:
            self.tileurls = []
            self.tileQimages = []
            self.tileRetryCount = 0

            radarpath = self.find_radar_path_for_time(t)
            if not radarpath:
                print(f'WARNING: {self.myname} no radar data available for time {t}')
                return False

            host = radarMetadataCache.get('host', '')
            if not host:
                print(f'WARNING: {self.myname} weather radar metadata has no host')
                return False

            for tt in self.tiletails:
                tileurl = host + radarpath + tt
                self.tileurls.append(tileurl)

        if self.getIndex >= len(self.tileurls):
            return False

        print(f'INFO: {self.myname} {t} tile{self.getIndex} {safeurl(self.tileurls[i])}')
        tilereq = QNetworkRequest(QUrl(self.tileurls[i]))
        self.tilereply = manager.get(tilereq)
        self.tilereply.finished.connect(self.get_tilesreply)
        return True

    def retry_tile_or_abandon_frame(self, reason):
        """Retry the current radar tile once, then abandon this frame."""
        print(f'WARNING: {self.myname} {reason} for tile {self.getIndex} at time {self.getTime}')

        if self.tileRetryCount < 1:
            self.tileRetryCount += 1
            print(f'INFO: {self.myname} retrying tile {self.getIndex} for time {self.getTime}')
            self.get_tiles(self.getTime, self.getIndex)
            return

        print(f'WARNING: {self.myname} abandoning radar frame {self.getTime} after failed tile retry')
        self.tileRetryCount = 0
        self.tileQimages = []
        self.tileurls = []
        self.get()

    @staticmethod
    def find_radar_path_for_time(timestamp):
        """Find the radar path from normalized metadata.

        Prefer an exact 10-minute frame. If that is unavailable, use the closest
        provider frame within five minutes.
        """
        paths = radarMetadataCache.get('paths', {})
        if not paths:
            return None

        timestamp = int(timestamp / 600) * 600
        if timestamp in paths:
            return paths[timestamp]

        closest_time = None
        closest_diff = float('inf')
        for frame_time in paths:
            time_diff = abs(frame_time - timestamp)
            if time_diff < closest_diff:
                closest_diff = time_diff
                closest_time = frame_time
                if time_diff == 0:
                    break

        if closest_time is not None and closest_diff <= 300:
            return paths[closest_time]

        return None

    def get_tilesreply(self):
        """Process the radar tile response"""
        if self.tilereply.error() != QNetworkReply.NoError:
            tilestr = str(self.tilereply.readAll(), 'utf-8')
            self.tilereply.deleteLater()
            self.retry_tile_or_abandon_frame(
                f'error response from weather radar provider: {tilestr}'
            )
            return

        tiledata = self.tilereply.readAll()
        tileimage = QImage()

        if not tileimage.loadFromData(tiledata) or tileimage.isNull():
            self.tilereply.deleteLater()
            self.retry_tile_or_abandon_frame('failed to load radar tile image')
            return

        if tileimage.format() != QImage.Format_ARGB32:
            tileimage = tileimage.convertToFormat(QImage.Format_ARGB32)

        self.tileQimages.append(tileimage)
        self.getIndex += 1
        self.tileRetryCount = 0
        self.tilereply.deleteLater()

        if self.getIndex < len(self.tileurls):
            self.get_tiles(self.getTime, self.getIndex)
        else:
            self.combine_tiles()
            self.get()

    def combine_tiles(self):
        """Combine the radar tiles into a single image"""
        ii = QImage(self.tilesWidth * 256, self.tilesHeight * 256, QImage.Format_ARGB32)
        ii.fill(Qt.transparent)  # initialize the image to be blank, otherwise it could contain garbage from old tiles
        painter = QPainter()
        painter.begin(ii)
        i = 0
        xo = self.cornerTiles['NW']['X']
        xo = int((int(xo) - xo) * 256)
        yo = self.cornerTiles['NW']['Y']
        yo = int((int(yo) - yo) * 256)
        for y in range(0, self.totalHeight, 256):
            for x in range(0, self.totalWidth, 256):
                try:
                    if not self.tileQimages[i].isNull():
                        painter.drawImage(x, y, self.tileQimages[i])
                    i += 1
                except IndexError:
                    print('WARNING:', traceback.format_exc())
                    pass
        painter.end()
        self.tileQimages = []

        frame_image = ii.copy(-xo, -yo, self.rect.width(), self.rect.height())
        radar_pixmap = QPixmap(frame_image)
        timestamp_pixmap = self.render_timestamp(frame_image.size())

        self.frameImages.append({
            'time': self.getTime,
            'image': radar_pixmap,
            'timestamp': timestamp_pixmap
        })

    def render_timestamp(self, size):
        """Create the timestamp label layer for a radar frame."""
        image = QImage(size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)

        painter = QPainter()
        painter.begin(image)
        provider = radarMetadataCache.get('provider', 'Weather Radar')
        timestamp = '{0:%H:%M} {1}'.format(datetime.datetime.fromtimestamp(self.getTime), provider)
        painter.setPen(QColor(63, 63, 63, 255))
        painter.setFont(QFont("Arial", pointSize=8, weight=75))
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.drawText(3 - 1, 12 - 1, timestamp)
        painter.drawText(3 + 2, 12 + 1, timestamp)
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawText(3, 12, timestamp)
        painter.drawText(3 + 1, 12, timestamp)
        painter.end()

        return QPixmap(image)

    def mapurl(self, rect, overlayonly):
        """
        Constructs and returns a URL based on the radar configuration, geographical
        bounds, and overlay option. The returned URL is determined by the overlay setting
        and external services like Mapbox or Google Maps.
        """
        if overlayonly:
            if usemapbox and self.radar.overlay != '':
                return self.mapboxoverlayurl(self.radar, rect)
            return ''

        if usemapbox:
            return self.mapboxbaseurl(self.radar, rect)

        return self.googlemapurl(self.radar, rect)

    @staticmethod
    def mapbox_base_style(radar):
        """Return the Mapbox base style for this radar."""
        return radar.basemap or 'mapbox/satellite-streets-v12'

    @staticmethod
    def google_base_style(radar):
        """Return the Google Static Maps map type for this radar."""
        return radar.basemap or 'hybrid'

    @staticmethod
    def mapboxbaseurl(radar, rect):
        """
        Constructs and returns a MapBox Static Tiles API URL for generating a classic map
        image based on the given radar config information and map dimensions.
        It uses the Google Maps zoom level system, adjusted by subtracting one for MapBox,
        which employs 512x512 tiles instead of 256x256.
        """
        if not hasattr(ApiKeys, 'mbapi'):
            return ''

        mbapi = ApiKeys.mbapi
        basemap = Radar.mapbox_base_style(radar)

        # if an overlay is specified, hide attribution on this base map
        hide_attribution = ''
        if radar.overlay != '':
            hide_attribution = '&attribution=false&logo=false'

        return 'https://api.mapbox.com/styles/v1/' + \
            basemap + \
            '/static/' + \
            str(radar.center.lng) + ',' + \
            str(radar.center.lat) + ',' + \
            str(radar.zoom - 1) + ',0,0/' + \
            str(rect.width()) + 'x' + str(rect.height()) + \
            '?access_token=' + mbapi + \
            hide_attribution

    @staticmethod
    def mapboxoverlayurl(radar, rect):
        """
        Constructs and returns a MapBox Static Tiles API URL for generating an overlay map
        image based on the given radar config information and map dimensions.
        It uses the Google Maps zoom level system, adjusted by subtracting one for MapBox,
        which employs 512x512 tiles instead of 256x256.
        """
        if not hasattr(ApiKeys, 'mbapi'):
            return ''

        mbapi = ApiKeys.mbapi

        if radar.overlay == '':
            return ''

        return 'https://api.mapbox.com/styles/v1/' + \
            radar.overlay + \
            '/static/' + \
            str(radar.center.lng) + ',' + \
            str(radar.center.lat) + ',' + \
            str(radar.zoom - 1) + ',0,0/' + \
            str(rect.width()) + 'x' + str(rect.height()) + \
            '?access_token=' + mbapi

    @staticmethod
    def googlemapurl(radar, rect):
        """
        Constructs and returns a Google Maps Static API URL for generating a static map
        image based on the given radar config information and map dimensions. The method
        adjusts the size of the resulting image if it exceeds the maximum allowed
        dimensions of 640x640 pixels, and adjusts the zoom level accordingly.
        """
        zoom = radar.zoom
        rsize = rect.size()

        if rsize.width() > 640 or rsize.height() > 640:
            rsize = QtCore.QSize(int(rsize.width() / 2), int(rsize.height() / 2))
            zoom -= 1

        urlp = [
            'center=' + str(radar.center.lat) + ',' + str(radar.center.lng),
            'zoom=' + str(zoom),
            'size=' + str(rsize.width()) + 'x' + str(rsize.height()),
            'maptype=' + Radar.google_base_style(radar)
        ]

        googleapi = getattr(ApiKeys, 'googleapi', '')
        if googleapi:
            urlp.insert(0, 'key=' + googleapi)

        return 'https://maps.googleapis.com/maps/api/staticmap?' + \
            '&'.join(urlp)

    def basefinished(self):
        if self.basereply.error() != QNetworkReply.NoError:
            basestr = str(self.basereply.readAll(), 'utf-8')
            if usemapbox:
                try:
                    basejson = json.loads(basestr)
                    print('ERROR: Response from api.mapbox.com: ' + basejson['message'])
                except ValueError:  # includes json.decoder.JSONDecodeError
                    print('ERROR: Response from api.mapbox.com: ' + basestr)
                    pass
            else:
                print('ERROR: Response from maps.googleapis.com: ' + basestr)
            return
        basepixmap = QPixmap()
        basepixmap.loadFromData(self.basereply.readAll())
        if basepixmap.size() != self.rect.size():
            basepixmap = basepixmap.scaled(self.rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(basepixmap)
        self.wmk.setPixmap(self.render_markers(basepixmap.size()))

    def render_markers(self, size):
        """Create the dimmed marker layer for this radar."""
        mkpixmap = QPixmap(size)
        mkpixmap.fill(Qt.transparent)

        painter = QPainter()
        painter.begin(mkpixmap)
        painter.fillRect(
            0,
            0,
            mkpixmap.width(),
            mkpixmap.height(),
            QBrush(QColor(Config.dimcolor))
        )

        for marker in self.radar.markers:
            self.draw_marker(painter, marker)

        painter.end()
        return mkpixmap

    def draw_marker(self, painter, marker):
        """Draw a single configured marker onto the marker layer."""
        if marker.get('visible', 1) != 1:
            return

        pt = get_point(
            marker['location'],
            self.point,
            self.zoom,
            self.rect.width(),
            self.rect.height()
        )
        marker_image = self.load_marker_image(marker)
        marker_height = self.radar.marker_height(marker)

        marker_image = marker_image.scaledToHeight(marker_height, 1)
        painter.drawImage(
            int(pt.x - marker_height / 2),
            int(pt.y - marker_height / 2),
            marker_image
        )

    def load_marker_image(self, marker):
        """Load, normalize, and optionally recolor a marker image."""
        marker_image = QImage()
        marker_image.load(self.radar.marker_image_file(marker))

        if marker_image.format != QImage.Format_ARGB32:
            marker_image = marker_image.convertToFormat(QImage.Format_ARGB32)

        if 'color' in marker:
            self.recolor_marker(marker_image, QColor(marker['color']))

        return marker_image

    @staticmethod
    def recolor_marker(marker_image, color):
        """Tint a marker image by multiplying RGB channels by the configured color."""
        cr, cg, cb, ca = color.getRgbF()
        for x in range(0, marker_image.width()):
            for y in range(0, marker_image.height()):
                r, g, b, a = QColor.fromRgba(marker_image.pixel(x, y)).getRgbF()
                r = r * cr
                g = g * cg
                b = b * cb
                marker_image.setPixel(x, y, QColor.fromRgbF(r, g, b, a).rgba())

    def overlayfinished(self):
        if self.overlayreply.error() != QNetworkReply.NoError:
            overlaystr = str(self.overlayreply.readAll(), 'utf-8')
            try:
                overlayjson = json.loads(overlaystr)
                print('ERROR: Response from api.mapbox.com: ' + overlayjson['message'])
            except ValueError:  # includes json.decoder.JSONDecodeError
                print('ERROR: Response from api.mapbox.com: ' + overlaystr)
                pass
            return
        overlaypixmap = QPixmap()
        overlaypixmap.loadFromData(self.overlayreply.readAll())
        if overlaypixmap.size() != self.rect.size():
            overlaypixmap = overlaypixmap.scaled(
                self.rect.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation)
        self.overlay.setPixmap(overlaypixmap)

    def getbase(self):
        basereq = QNetworkRequest(QUrl(self.baseurl))
        self.basereply = manager.get(basereq)
        self.basereply.finished.connect(self.basefinished)

    def getoverlay(self):
        overlayreq = QNetworkRequest(QUrl(self.overlayurl))
        self.overlayreply = manager.get(overlayreq)
        self.overlayreply.finished.connect(self.overlayfinished)

    def start(self, interval=0):
        """Start the radar display with an optional interval override"""
        if interval > 0:
            self.interval = interval
        self.getbase()

        if usemapbox and self.radar.overlay != '':
            self.getoverlay()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.rtick)
        self.lastget = time.time() - self.interval + random.uniform(3, 10)

    def wxstart(self):
        print('INFO: wxstart for ' + self.myname)
        self.timer.start(200)

    def wxstop(self):
        print('INFO: wxstop for ' + self.myname)
        self.timer.stop()

    def stop(self):
        try:
            self.timer.stop()
            self.timer = None
        except AttributeError:
            print('WARNING:', traceback.format_exc())
            pass


def realquit():
    QtWidgets.QApplication.exit(0)


def myquit(signum, frame):
    objradar1.stop()
    objradar2.stop()
    objradar3.stop()
    objradar4.stop()
    ctimer.stop()
    wxtimer.stop()
    temptimer.stop()
    if Config.useslideshow:
        objimage1.stop()

    QtCore.QTimer.singleShot(30, realquit)


def fixupframe(frame, onoff):
    def find_radars(widget):
        """Recursively find all Radar objects in the widget tree"""
        radars = []
        for child in widget.children():
            if isinstance(child, Radar):
                radars.append(child)
            else:
                radars.extend(find_radars(child))
        return radars

    for radar in find_radars(frame):
        if onoff:
            # print('INFO: calling wxstart on ' + radar.myname + ' in ' + frame.objectName())
            radar.wxstart()
        else:
            # print('INFO: calling wxstop on ' + radar.myname + ' in ' + frame.objectName())
            radar.wxstop()


def nextframe(plusminus):
    global framep
    frames[framep].setVisible(False)
    fixupframe(frames[framep], onoff=False)
    framep += plusminus
    if framep >= len(frames):
        framep = 0
    if framep < 0:
        framep = len(frames) - 1
    frames[framep].setVisible(True)
    fixupframe(frames[framep], onoff=True)


class MyMain(QtWidgets.QWidget):

    def keyPressEvent(self, event):
        global weatherplayer, lastkeytime
        if isinstance(event, QtGui.QKeyEvent):
            # print('INFO:', event.key(), format(event.key(), '08x'))
            if event.key() == Qt.Key_F4:
                myquit(signal.SIGINT, None)
            if event.key() == Qt.Key_F2:
                if time.time() > lastkeytime:
                    if weatherplayer is None:
                        weatherplayer = Popen(
                            ['mpg123', '-q', Config.noaastream])
                    else:
                        weatherplayer.kill()
                        weatherplayer = None
                lastkeytime = time.time() + 2
            if event.key() == Qt.Key_Space:
                nextframe(1)
            if event.key() == Qt.Key_Left:
                nextframe(-1)
            if event.key() == Qt.Key_Right:
                nextframe(1)
            if event.key() == Qt.Key_F6:  # Previous Image
                objimage1.prev_next(-1)
            if event.key() == Qt.Key_F7:  # Next Image
                objimage1.prev_next(1)
            if event.key() == Qt.Key_F8:  # Play/Pause
                objimage1.play_pause()
            if event.key() == Qt.Key_F9:  # Foreground Toggle
                if foreGround.isVisible():
                    foreGround.hide()
                else:
                    foreGround.show()

    def mousePressEvent(self, event):
        if isinstance(event, QtGui.QMouseEvent):
            nextframe(1)


configname = 'Config'

if len(sys.argv) > 1:
    configname = sys.argv[1]

if not os.path.isfile(configname + '.py'):
    print('ERROR: Config file not found %s' % configname + '.py')
    exit(1)

Config = __import__(configname)

# define default values for new/optional config variables.

try:
    Config.metric
except AttributeError:
    Config.metric = 0

try:
    Config.weather_refresh
except AttributeError:
    Config.weather_refresh = 30  # minutes

try:
    Config.radar_refresh
except AttributeError:
    Config.radar_refresh = 10  # minutes

try:
    Config.userainviewer
except AttributeError:
    Config.userainviewer = 0

try:
    Config.useopenmeteo
except AttributeError:
    Config.useopenmeteo = 0

try:
    Config.fontattr
except AttributeError:
    Config.fontattr = ''

try:
    Config.dimcolor
except AttributeError:
    Config.dimcolor = QColor('#000000')
    Config.dimcolor.setAlpha(0)

try:
    Config.DateLocale
except AttributeError:
    Config.DateLocale = ''

try:
    Config.wind_degrees
except AttributeError:
    Config.wind_degrees = 0

try:
    Config.pressure_mbar
except AttributeError:
    Config.pressure_mbar = Config.metric

try:
    Config.digital
except AttributeError:
    Config.digital = 0

try:
    Config.Language
except AttributeError:
    Config.Language = 'EN'

try:
    Config.fontmult
except AttributeError:
    Config.fontmult = 1.0

try:
    Config.LPressure
except AttributeError:
    Config.LPressure = 'Pressure '
    Config.LHumidity = 'Humidity '
    Config.LWind = 'Wind '
    Config.Lgusting = ' gust '
    Config.LFeelslike = 'Feels like '
    Config.LPrecip1hr = ' Precip 1hr:'
    Config.LToday = 'Today: '
    Config.LSunRise = 'Sun Rise: '
    Config.LSet = ' Set: '
    Config.LMoonPhase = ' Moon: '
    Config.LInsideTemp = 'Inside Temp '
    Config.LRain = ' Rain: '
    Config.LSnow = ' Snow: '

try:
    Config.Lmoon1
    Config.Lmoon2
    Config.Lmoon3
    Config.Lmoon4
    Config.Lmoon5
    Config.Lmoon6
    Config.Lmoon7
    Config.Lmoon8
except AttributeError:
    Config.Lmoon1 = 'New Moon'
    Config.Lmoon2 = 'Waxing Crescent'
    Config.Lmoon3 = 'First Quarter'
    Config.Lmoon4 = 'Waxing Gibbous'
    Config.Lmoon5 = 'Full Moon'
    Config.Lmoon6 = 'Waning Gibbous'
    Config.Lmoon7 = 'Third Quarter'
    Config.Lmoon8 = 'Waning Crescent'

try:
    Config.digitalformat2
except AttributeError:
    Config.digitalformat2 = '{0:%H:%M:%S}'

try:
    Config.useslideshow
except AttributeError:
    Config.useslideshow = 0

try:
    Config.startup_screen
except AttributeError:
    Config.startup_screen = 1

# Check if Mapbox API key is set, and use mapbox if so
usemapbox = 0
try:
    if ApiKeys.mbapi[:3].lower() == 'pk.':
        usemapbox = 1
except AttributeError:
    pass

hasMetar = False
try:
    if Config.METAR != '':
        hasMetar = True
        from metar import Metar
except AttributeError:
    pass

lastmin = -1
lastday = -1
pdy = ''
lasttimestr = ''
weatherplayer = None
lastkeytime = 0
lastapiget = time.time()

app = QtWidgets.QApplication(sys.argv)
desktop = app.desktop()
rec = desktop.screenGeometry()
height = rec.height()
width = rec.width()

signal.signal(signal.SIGINT, myquit)

w = MyMain()
w.setWindowTitle(os.path.basename(__file__))

w.setStyleSheet('QWidget { background-color: black;}')

xscale = float(width) / 1440.0
yscale = float(height) / 900.0

frames = []
framep = Config.startup_screen - 1  # Convert from 1-based to 0-based indexing

frame1 = QtWidgets.QFrame(w)
frame1.setObjectName('frame1')
frame1.setGeometry(0, 0, width, height)
frame1.setStyleSheet('#frame1 { background-color: black; border-image: url(' +
                     Config.background + ') 0 0 0 0 stretch stretch;}')
frames.append(frame1)

if Config.useslideshow:
    imgRect = QtCore.QRect(0, 0, int(width), int(height))
    objimage1 = SlideShow(frame1, imgRect, 'image1')

frame2 = QtWidgets.QFrame(w)
frame2.setObjectName('frame2')
frame2.setGeometry(0, 0, width, height)
frame2.setStyleSheet('#frame2 { background-color: black; border-image: url(' +
                     Config.background + ') 0 0 0 0 stretch stretch;}')
frame2.setVisible(False)
frames.append(frame2)

# Set visibility based on startup_screen
if 0 <= framep < len(frames):
    for i, frame in enumerate(frames):
        if i == framep:
            frame.setVisible(True)
            fixupframe(frame, onoff=True)
        else:
            frame.setVisible(False)

foreGround = QtWidgets.QFrame(frame1)
foreGround.setObjectName('foreGround')
foreGround.setStyleSheet('#foreGround { background-color: transparent; }')
foreGround.setGeometry(0, 0, width, height)

squares1 = QtWidgets.QFrame(foreGround)
squares1.setObjectName('squares1')
squares1.setGeometry(0, int(height - yscale * 600), int(xscale * 340), int(yscale * 600))
squares1.setStyleSheet(
    '#squares1 { background-color: transparent; border-image: url(' +
    Config.squares1 +
    ') 0 0 0 0 stretch stretch;}')

squares2 = QtWidgets.QFrame(foreGround)
squares2.setObjectName('squares2')
squares2.setGeometry(int(width - xscale * 340), 0, int(xscale * 340), int(yscale * 900))
squares2.setStyleSheet(
    '#squares2 { background-color: transparent; border-image: url(' +
    Config.squares2 +
    ') 0 0 0 0 stretch stretch;}')

if not Config.digital:
    clockface = QtWidgets.QFrame(foreGround)
    clockface.setObjectName('clockface')
    clockrect = QtCore.QRect(
        int(width / 2 - height * .4),
        int(height * .45 - height * .4),
        int(height * .8),
        int(height * .8))
    clockface.setGeometry(clockrect)
    clockface.setStyleSheet(
        '#clockface { background-color: transparent; border-image: url(' +
        Config.clockface +
        ') 0 0 0 0 stretch stretch;}')

    hourhand = QtWidgets.QLabel(foreGround)
    hourhand.setObjectName('hourhand')
    hourhand.setStyleSheet('#hourhand { background-color: transparent; }')

    minhand = QtWidgets.QLabel(foreGround)
    minhand.setObjectName('minhand')
    minhand.setStyleSheet('#minhand { background-color: transparent; }')

    sechand = QtWidgets.QLabel(foreGround)
    sechand.setObjectName('sechand')
    sechand.setStyleSheet('#sechand { background-color: transparent; }')

    hourpixmap = QtGui.QPixmap(Config.hourhand)
    hourpixmap2 = QtGui.QPixmap(Config.hourhand)
    minpixmap = QtGui.QPixmap(Config.minhand)
    minpixmap2 = QtGui.QPixmap(Config.minhand)
    secpixmap = QtGui.QPixmap(Config.sechand)
    secpixmap2 = QtGui.QPixmap(Config.sechand)
else:
    clockface = QtWidgets.QLabel(foreGround)
    clockface.setObjectName('clockface')
    clockrect = QtCore.QRect(
        int(width / 2 - height * .4),
        int(height * .45 - height * .4),
        int(height * .8),
        int(height * .8))
    clockface.setGeometry(clockrect)
    dcolor = QColor(Config.digitalcolor).darker(0).name()
    lcolor = QColor(Config.digitalcolor).lighter(120).name()
    clockface.setStyleSheet(
        '#clockface { background-color: transparent; font-family:sans-serif;' +
        ' font-weight: light; color: ' +
        lcolor +
        '; background-color: transparent; font-size: ' +
        str(int(Config.digitalsize * xscale)) +
        'px; ' +
        Config.fontattr +
        '}')
    clockface.setAlignment(Qt.AlignCenter)
    clockface.setGeometry(clockrect)
    glow = QtWidgets.QGraphicsDropShadowEffect()
    glow.setOffset(0)
    glow.setBlurRadius(50)
    glow.setColor(QColor(dcolor))
    clockface.setGraphicsEffect(glow)

radar1rect = QtCore.QRect(int(3 * xscale), int(344 * yscale), int(300 * xscale), int(275 * yscale))
objradar1 = Radar(foreGround, Config.radar1, radar1rect, 'radar1')

radar2rect = QtCore.QRect(int(3 * xscale), int(622 * yscale), int(300 * xscale), int(275 * yscale))
objradar2 = Radar(foreGround, Config.radar2, radar2rect, 'radar2')

radar3rect = QtCore.QRect(int(13 * xscale), int(50 * yscale), int(700 * xscale), int(700 * yscale))
objradar3 = Radar(frame2, Config.radar3, radar3rect, 'radar3')

radar4rect = QtCore.QRect(int(726 * xscale), int(50 * yscale), int(700 * xscale), int(700 * yscale))
objradar4 = Radar(frame2, Config.radar4, radar4rect, 'radar4')

datex = QtWidgets.QLabel(foreGround)
datex.setObjectName('datex')
datex.setStyleSheet('#datex { font-family:sans-serif; color: ' +
                    Config.textcolor +
                    '; background-color: transparent; font-size: ' +
                    str(int(50 * xscale * Config.fontmult)) +
                    'px; ' +
                    Config.fontattr +
                    '}')
datex.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
datex.setGeometry(0, 0, width, int(100 * yscale))

datex2 = QtWidgets.QLabel(frame2)
datex2.setObjectName('datex2')
datex2.setStyleSheet('#datex2 { font-family:sans-serif; color: ' +
                     Config.textcolor +
                     '; background-color: transparent; font-size: ' +
                     str(int(50 * xscale * Config.fontmult)) + 'px; ' +
                     Config.fontattr +
                     '}')
datex2.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
datex2.setGeometry(int(800 * xscale), int(760 * yscale), int(640 * xscale), 100)
datey2 = QtWidgets.QLabel(frame2)
datey2.setObjectName('datey2')
datey2.setStyleSheet('#datey2 { font-family:sans-serif; color: ' +
                     Config.textcolor +
                     '; background-color: transparent; font-size: ' +
                     str(int(50 * xscale * Config.fontmult)) +
                     'px; ' +
                     Config.fontattr +
                     '}')
datey2.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
datey2.setGeometry(int(800 * xscale), int(820 * yscale), int(640 * xscale), 100)

ypos = -25
wxicon = QtWidgets.QLabel(foreGround)
wxicon.setObjectName('wxicon')
wxicon.setStyleSheet('#wxicon { background-color: transparent; }')
wxicon.setGeometry(int(75 * xscale), int(ypos * yscale), int(150 * xscale), int(150 * yscale))

attribution = QtWidgets.QLabel(foreGround)
attribution.setObjectName('attribution')
attribution.setStyleSheet('#attribution { ' +
                          ' background-color: transparent; color: ' +
                          Config.textcolor +
                          '; font-size: ' +
                          str(int(12 * xscale)) +
                          'px; ' +
                          Config.fontattr +
                          '}')
attribution.setAlignment(Qt.AlignTop)
attribution.setGeometry(int(6 * xscale), int(3 * yscale), int(130 * xscale), 100)

wxicon2 = QtWidgets.QLabel(frame2)
wxicon2.setObjectName('wxicon2')
wxicon2.setStyleSheet('#wxicon2 { background-color: transparent; }')
wxicon2.setGeometry(int(0 * xscale), int(750 * yscale), int(150 * xscale), int(150 * yscale))

attribution2 = QtWidgets.QLabel(frame2)
attribution2.setObjectName('attribution2')
attribution2.setStyleSheet('#attribution2 { ' +
                           'background-color: transparent; color: ' +
                           Config.textcolor +
                           '; font-size: ' +
                           str(int(12 * xscale * Config.fontmult)) +
                           'px; ' +
                           Config.fontattr +
                           '}')
attribution2.setAlignment(Qt.AlignTop)
attribution2.setGeometry(int(6 * xscale), int(880 * yscale), int(130 * xscale), 100)

ypos += 130
wxdesc = QtWidgets.QLabel(foreGround)
wxdesc.setObjectName('wxdesc')
wxdesc.setStyleSheet('#wxdesc { background-color: transparent; color: ' +
                     Config.textcolor +
                     '; font-size: ' +
                     str(int(30 * xscale)) +
                     'px; ' +
                     Config.fontattr +
                     '}')
wxdesc.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
wxdesc.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

wxdesc2 = QtWidgets.QLabel(frame2)
wxdesc2.setObjectName('wxdesc2')
wxdesc2.setStyleSheet('#wxdesc2 { background-color: transparent; color: ' +
                      Config.textcolor +
                      '; font-size: ' +
                      str(int(50 * xscale * Config.fontmult)) +
                      'px; ' +
                      Config.fontattr +
                      '}')
wxdesc2.setAlignment(Qt.AlignLeft | Qt.AlignTop)
wxdesc2.setGeometry(int(400 * xscale), int(800 * yscale), int(400 * xscale), 100)

ypos += 25
temper = QtWidgets.QLabel(foreGround)
temper.setObjectName('temper')
temper.setStyleSheet('#temper { background-color: transparent; color: ' +
                     Config.textcolor +
                     '; font-size: ' +
                     str(int(70 * xscale * Config.fontmult)) +
                     'px; ' +
                     Config.fontattr +
                     '}')
temper.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
temper.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), int(100 * yscale))

temper2 = QtWidgets.QLabel(frame2)
temper2.setObjectName('temper2')
temper2.setStyleSheet('#temper2 { background-color: transparent; color: ' +
                      Config.textcolor +
                      '; font-size: ' +
                      str(int(70 * xscale * Config.fontmult)) +
                      'px; ' +
                      Config.fontattr +
                      '}')
temper2.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
temper2.setGeometry(int(125 * xscale), int(780 * yscale), int(300 * xscale), int(100 * yscale))

ypos += 80
press = QtWidgets.QLabel(foreGround)
press.setObjectName('press')
press.setStyleSheet('#press { background-color: transparent; color: ' +
                    Config.textcolor +
                    '; font-size: ' +
                    str(int(25 * xscale * Config.fontmult)) +
                    'px; ' +
                    Config.fontattr +
                    '}')
press.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
press.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

ypos += 30
humidity = QtWidgets.QLabel(foreGround)
humidity.setObjectName('humidity')
humidity.setStyleSheet('#humidity { background-color: transparent; color: ' +
                       Config.textcolor +
                       '; font-size: ' +
                       str(int(25 * xscale * Config.fontmult)) +
                       'px; ' +
                       Config.fontattr +
                       '}')
humidity.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
humidity.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

ypos += 30
wind = QtWidgets.QLabel(foreGround)
wind.setObjectName('wind')
wind.setStyleSheet('#wind { background-color: transparent; color: ' +
                   Config.textcolor +
                   '; font-size: ' +
                   str(int(20 * xscale * Config.fontmult)) +
                   'px; ' +
                   Config.fontattr +
                   '}')
wind.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
wind.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

ypos += 20
feelslike = QtWidgets.QLabel(foreGround)
feelslike.setObjectName('feelslike')
feelslike.setStyleSheet('#feelslike { background-color: transparent; color: ' +
                        Config.textcolor +
                        '; font-size: ' +
                        str(int(20 * xscale * Config.fontmult)) +
                        'px; ' +
                        Config.fontattr +
                        '}')
feelslike.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
feelslike.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

ypos += 20
wdate = QtWidgets.QLabel(foreGround)
wdate.setObjectName('wdate')
wdate.setStyleSheet('#wdate { background-color: transparent; color: ' +
                    Config.textcolor +
                    '; font-size: ' +
                    str(int(15 * xscale * Config.fontmult)) +
                    'px; ' +
                    Config.fontattr +
                    '}')
wdate.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
wdate.setGeometry(int(3 * xscale), int(ypos * yscale), int(300 * xscale), 100)

bottom = QtWidgets.QLabel(foreGround)
bottom.setObjectName('bottom')
bottom.setStyleSheet('#bottom { font-family:sans-serif; color: ' +
                     Config.textcolor +
                     '; background-color: transparent; font-size: ' +
                     str(int(30 * xscale * Config.fontmult)) +
                     'px; ' +
                     Config.fontattr +
                     '}')
bottom.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
bottom.setGeometry(0, int(height - 50 * yscale), width, int(50 * yscale))

temp = QtWidgets.QLabel(foreGround)
temp.setObjectName('temp')
temp.setStyleSheet('#temp { font-family:sans-serif; color: ' +
                   Config.textcolor +
                   '; background-color: transparent; font-size: ' +
                   str(int(30 * xscale * Config.fontmult)) +
                   'px; ' +
                   Config.fontattr +
                   '}')
temp.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
temp.setGeometry(0, int(height - 100 * yscale), width, int(50 * yscale))

owmonecall = True
tzlatlng = pytz.utc
forecast = []

for i in range(0, 9):
    lab = QtWidgets.QLabel(foreGround)
    lab.setObjectName('forecast' + str(i))
    lab.setStyleSheet('QWidget { background-color: transparent; color: ' +
                      Config.textcolor +
                      '; font-size: ' +
                      str(int(20 * xscale * Config.fontmult)) +
                      'px; ' +
                      Config.fontattr +
                      '}')
    lab.setGeometry(int(1137 * xscale), int(i * 100 * yscale), int(300 * xscale), int(100 * yscale))

    icon = QtWidgets.QLabel(lab)
    icon.setStyleSheet('#icon { background-color: transparent; }')
    icon.setGeometry(0, 0, int(100 * xscale), int(100 * yscale))
    icon.setObjectName('icon')

    wx = QtWidgets.QLabel(lab)
    wx.setStyleSheet('#wx { background-color: transparent; }')
    wx.setGeometry(int(100 * xscale), int(5 * yscale), int(200 * xscale), int(120 * yscale))
    wx.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    wx.setWordWrap(True)
    wx.setObjectName('wx')

    day = QtWidgets.QLabel(lab)
    day.setStyleSheet('#day { background-color: transparent; }')
    day.setGeometry(int(100 * xscale), int(75 * yscale), int(200 * xscale), int(25 * yscale))
    day.setAlignment(Qt.AlignRight | Qt.AlignBottom)
    day.setObjectName('day')

    forecast.append(lab)

manager = QtNetwork.QNetworkAccessManager()

stimer = QtCore.QTimer()
stimer.singleShot(10, qtstart)

w.show()
w.showFullScreen()

sys.exit(app.exec_())
