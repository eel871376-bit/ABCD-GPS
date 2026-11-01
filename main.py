import math
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

EARTH_R = 6371000.0
TICK = 0.1          # 이동 계산 주기(초)
IDLE_SEND = 1.0     # 정지 중 위치 유지 전송 간격(초)
DEADZONE = 0.08


def destination_point(lat, lon, bearing_deg, dist_m):
    """방위각/거리만큼 이동한 좌표 계산 (구면 근사)"""
    br = math.radians(bearing_deg)
    d = dist_m / EARTH_R
    la1, lo1 = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(d) +
                    math.cos(la1) * math.sin(d) * math.cos(br))
    lo2 = lo1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(la1),
                           math.cos(d) - math.sin(la1) * math.sin(la2))
    lo2 = (lo2 + 3 * math.pi) % (2 * math.pi) - math.pi
    return math.degrees(la2), math.degrees(lo2)


class MockGps:
    """안드로이드 LocationManager 테스트 프로바이더 래퍼 (pyjnius)"""
    def __init__(self):
        self.ok = False
        self.err = ""
        if platform != "android":
            self.err = "안드로이드가 아님 (전송 생략)"
            return
        try:
            from jnius import autoclass
            self.Location = autoclass("android.location.Location")
            self.SystemClock = autoclass("android.os.SystemClock")
            self.System = autoclass("java.lang.System")
            Context = autoclass("android.content.Context")
            LM = autoclass("android.location.LocationManager")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            self.lm = act.getSystemService(Context.LOCATION_SERVICE)
            self.prov = LM.GPS_PROVIDER
            try:
                self.lm.removeTestProvider(self.prov)
            except Exception:
                pass
            # (이름, 네트워크, 위성, 셀, 요금, 고도, 속도, 방위, 전력, 정확도)
            self.lm.addTestProvider(self.prov, False, False, False, False,
                                    True, True, True, 1, 1)
            self.lm.setTestProviderEnabled(self.prov, True)
            self.ok = True
        except Exception as e:
            self.err = "모의 위치 초기화 실패: %s" % e

    def send(self, lat, lon, bearing, speed_ms):
        if not self.ok:
            return
        try:
            loc = self.Location(self.prov)
            loc.setLatitude(lat)
            loc.setLongitude(lon)
            loc.setAltitude(30.0)
            loc.setAccuracy(3.0)
            loc.setBearing(float(bearing))
            loc.setSpeed(float(speed_ms))
            loc.setTime(self.System.currentTimeMillis())
            loc.setElapsedRealtimeNanos(self.SystemClock.elapsedRealtimeNanos())
            self.lm.setTestProviderLocation(self.prov, loc)
        except Exception as e:
            self.err = "전송 실패: %s" % e

    def close(self):
        if self.ok:
            try:
                self.lm.removeTestProvider(self.prov)
            except Exception:
                pass


class Joystick(Widget):
    ex = NumericProperty(0)   # 동(+)/서(-)  -1~1
    ny = NumericProperty(0)   # 북(+)/남(-)  -1~1

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw, ex=self._draw, ny=self._draw)

    def _radius(self):
        return min(self.size) / 2 * 0.8

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center
        r = self._radius()
        kr = r * 0.3
        with self.canvas:
            Color(0.2, 0.2, 0.25, 1)
            Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
            Color(0.3, 0.64, 1, 1)
            Ellipse(pos=(cx + self.ex * r - kr, cy + self.ny * r - kr),
                    size=(2 * kr, 2 * kr))

    def _set(self, touch):
        cx, cy = self.center
        r = self._radius()
        dx, dy = touch.x - cx, touch.y - cy
        m = math.hypot(dx, dy)
        if m > r:
            dx, dy = dx / m * r, dy / m * r
        self.ex, self.ny = dx / r, dy / r

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.grab(self)
            self._set(touch)
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self._set(touch)
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self.ex, self.ny = 0, 0     # 놓으면 중앙 복귀
            return True
        return super().on_touch_up(touch)


class GpsApp(App):
    def build(self):
        self.gps = None
        self.lat, self.lon = 37.5665, 126.9780
        self.bearing = 0.0
        self.event = None
        self.idle_acc = 0.0

        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(6))

        def field(hint, text):
            t = TextInput(text=text, hint_text=hint, multiline=False,
                          input_filter="float", size_hint_y=None, height=dp(44))
            root.add_widget(t)
            return t

        self.in_speed = field("최대 시속 (km/h)", "5.0")
        self.in_lat = field("위도", str(self.lat))
        self.in_lon = field("경도", str(self.lon))

        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.btn_start = Button(text="Start", on_release=self.start)
        self.btn_stop = Button(text="Stop", on_release=self.stop, disabled=True)
        row.add_widget(self.btn_start)
        row.add_widget(self.btn_stop)
        root.add_widget(row)

        self.joy = Joystick()
        root.add_widget(self.joy)

        self.lbl = Label(text="정지됨", size_hint_y=None, height=dp(60))
        root.add_widget(self.lbl)
        return root

    def start(self, *_):
        try:
            self.lat = float(self.in_lat.text)
            self.lon = float(self.in_lon.text)
            float(self.in_speed.text)
            assert -90 <= self.lat <= 90 and -180 <= self.lon <= 180
        except Exception:
            self.lbl.text = "입력값 오류"
            return

        if platform == "android":      # 위치 권한 요청
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.ACCESS_FINE_LOCATION,
                                 Permission.ACCESS_COARSE_LOCATION])

        self.gps = MockGps()
        if not self.gps.ok and self.gps.err:
            self.lbl.text = self.gps.err
        self.btn_start.disabled = True
        self.btn_stop.disabled = False
        self.last = time.monotonic()
        self.idle_acc = 0.0
        self.event = Clock.schedule_interval(self.tick, TICK)

    def stop(self, *_):
        if self.event:
            self.event.cancel()
            self.event = None
        if self.gps:
            self.gps.close()
            self.gps = None
        self.in_lat.text = "%.6f" % self.lat
        self.in_lon.text = "%.6f" % self.lon
        self.btn_start.disabled = False
        self.btn_stop.disabled = True
        self.lbl.text = "정지됨"

    def tick(self, _dt):
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        try:
            vmax = float(self.in_speed.text)
        except ValueError:
            vmax = 0.0

        ex, ny = self.joy.ex, self.joy.ny
        mag = min(1.0, math.hypot(ex, ny))
        kmh = 0.0
        if mag > DEADZONE and vmax > 0:
            self.bearing = (math.degrees(math.atan2(ex, ny)) + 360) % 360
            kmh = vmax * mag                    # 기울인 만큼 비례
            dist = (kmh / 3.6) * dt             # km/h -> m/s -> m
            self.lat, self.lon = destination_point(
                self.lat, self.lon, self.bearing, dist)
            self.gps.send(self.lat, self.lon, self.bearing, kmh / 3.6)
            self.idle_acc = 0.0
        else:
            self.idle_acc += dt                 # 정지 중에도 주기적으로 위치 유지
            if self.idle_acc >= IDLE_SEND:
                self.idle_acc = 0.0
                self.gps.send(self.lat, self.lon, self.bearing, 0.0)

        self.lbl.text = "%.6f, %.6f\n방위각 %.0f°  속도 %.1f km/h" % (
            self.lat, self.lon, self.bearing, kmh)

    def on_stop(self):
        self.stop()


if __name__ == "__main__":
    GpsApp().run()
