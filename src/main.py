# %%
import datetime as dt
import multiprocessing
import os
import sys
import threading
import webbrowser
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from itertools import zip_longest

import pandas as pd
import wx
import wx.adv
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.join(sys._MEIPASS, "src")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

ICON_PATH = os.path.join(BASE_DIR, "favicon.ico")
PAGE_URL = os.environ["PAGE_URL"]


class WorkingStatus(StrEnum):
    working = "working"
    resting = "resting"


@dataclass
class SapResponse:
    worked_time: str
    left_time: str
    overtime: str
    working_status: WorkingStatus
    entries_number: str


def grouper(iterable, n, *, incomplete="fill", fillvalue=None):
    iterators = [iter(iterable)] * n
    match incomplete:
        case "fill":
            return zip_longest(*iterators, fillvalue=fillvalue)
        case "strict":
            return zip(*iterators, strict=True)
        case "ignore":
            return zip(*iterators)
        case _:
            raise ValueError("Expected fill, strict, or ignore")


def format_dt(dt):
    return str(dt.round("s")).split("days")[1].strip()


def get_times() -> SapResponse:
    options = webdriver.EdgeOptions()
    options.use_chromium = True
    options.add_argument("headless")
    options.add_argument("disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    with webdriver.Edge(options=options) as driver:
        driver.get(PAGE_URL)
        driver.implicitly_wait(2)
        WebDriverWait(driver, 2).until(EC.visibility_of_element_located((By.PARTIAL_LINK_TEXT, "My Time Events"))).click()
        WebDriverWait(driver, 2).until(EC.visibility_of_element_located((By.XPATH, '//*[@id="__xmlview0--overview-text"]'))).click()
        WebDriverWait(driver, 2).until(EC.invisibility_of_element_located((By.XPATH, "//*[contains(text(), 'No data')]")))
        table_html = driver.find_element(By.ID, "__xmlview0--idEventsTable")
        string_buffer = StringIO(table_html.get_attribute("outerHTML"))

    df = pd.read_html(string_buffer)[0]
    if df.shape[0] > 0 and df.shape[0] % 2 != 0:
        working_status = WorkingStatus.working
    else:
        working_status = WorkingStatus.resting

    dates = df["Date & Time"]
    fmt = "%d.%m.%YObject Identifier%H:%M:%S"
    dates_fmt = pd.to_datetime(dates, format=fmt)
    timedeltas = []
    for group in grouper(dates_fmt, 2, fillvalue=pd.Timestamp(dt.datetime.now())):
        sorted_group = sorted(group)
        timedeltas.append(sorted_group[1] - sorted_group[0])

    worked_time: str = sum(timedeltas, pd.Timedelta(0))
    left_time = pd.Timedelta(hours=8) - worked_time
    overtime = pd.Timedelta(seconds=0)
    if left_time < pd.Timedelta(seconds=0):
        left_time = pd.Timedelta(seconds=0)
        overtime = worked_time - pd.Timedelta(hours=8)

    return SapResponse(
        worked_time=format_dt(worked_time),
        left_time=format_dt(left_time),
        overtime=format_dt(overtime),
        working_status=working_status,
        entries_number=f"{df.shape[0]}",
    )


class TimeTrackerApp(wx.Frame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.SetTitle("Time Tracker")
        self.SetSize((200, 260))
        self.SetWindowStyleFlag(wx.SYSTEM_MENU | wx.SYSTEM_MENU | wx.CAPTION | wx.CLOSE_BOX)
        icon = wx.Icon(ICON_PATH)
        self.SetIcon(icon)
        self.Centre()

        self.panel = wx.Panel(self)

        wx.StaticText(self.panel, label="Time Worked:", pos=(15, 10))
        self.worked_time_value = wx.StaticText(self.panel, label="00:00:00", pos=(120, 10))

        wx.StaticText(self.panel, label="Time Left:", pos=(15, 40))
        self.left_time_value = wx.StaticText(self.panel, label="00:00:00", pos=(120, 40))

        wx.StaticText(self.panel, label="Overtime:", pos=(15, 70))
        self.overtime_value = wx.StaticText(self.panel, label="00:00:00", pos=(120, 70))

        wx.StaticText(self.panel, label="Working Status:", pos=(15, 100))
        self.working_status_value = wx.StaticText(self.panel, label=WorkingStatus.resting, pos=(120, 100))
        self.working_status_value.SetBackgroundColour(wx.Colour(255, 153, 51))

        wx.StaticText(self.panel, label="SAP entries:", pos=(15, 130))
        self.entries_value = wx.StaticText(self.panel, label="0", pos=(120, 130))

        self.update_button = wx.Button(self.panel, label="Synchronize", pos=(45, 160))
        self.update_button.Bind(wx.EVT_BUTTON, self.button_synchronize)

        self.link = wx.adv.HyperlinkCtrl(self.panel, id=wx.ID_ANY, label="View Source", url=PAGE_URL, pos=(65, 190))

        self._update_running = False

        self.tray_icon = TimeTrackerTrayIcon(self)

        self.timer_update_data = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_synchronize, self.timer_update_data)
        self.timer_update_data.Start(60 * 60 * 1000)

        self.timer_visual_clock = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.visual_clock, self.timer_visual_clock)
        self.timer_visual_clock.Start(1 * 1000)

        self.Bind(wx.EVT_CLOSE, self.on_close)

        wx.CallAfter(self.button_synchronize, None)

    def visual_clock(self, event):
        time_format = "%H:%M:%S"
        if self.working_status_value.GetLabel() != WorkingStatus.resting:
            worked_time = dt.datetime.strptime(self.worked_time_value.GetLabel(), time_format) + dt.timedelta(seconds=1)

            left_time = dt.datetime.strptime(self.left_time_value.GetLabel(), time_format)
            if left_time.time() > dt.time():
                left_time -= dt.timedelta(seconds=1)

            overtime = dt.datetime.strptime(self.overtime_value.GetLabel(), time_format)
            if overtime.time() > dt.time() or left_time.time() == dt.time():
                overtime += dt.timedelta(seconds=1)

            wx.CallAfter(self.worked_time_value.SetLabel, worked_time.strftime(time_format))
            wx.CallAfter(self.left_time_value.SetLabel, left_time.strftime(time_format))
            wx.CallAfter(self.overtime_value.SetLabel, overtime.strftime(time_format))

    def fetch_time_data(self):
        try:
            response = get_times()
        except Exception as e:
            print(f"Error fetching data: {e}")
            response = SapResponse(
                worked_time=self.worked_time_value.GetLabel(),
                left_time=self.left_time_value.GetLabel(),
                overtime=self.overtime_value.GetLabel(),
                working_status=self.working_status_value.GetLabel(),
                entries_number=self.entries_value.GetLabel(),
            )

        return response

    def update_gui(self, sap_response: SapResponse):
        wx.CallAfter(self.worked_time_value.SetLabel, sap_response.worked_time)
        wx.CallAfter(self.left_time_value.SetLabel, sap_response.left_time)
        wx.CallAfter(self.overtime_value.SetLabel, sap_response.overtime)
        if sap_response.working_status == WorkingStatus.resting:
            wx.CallAfter(self.working_status_value.SetBackgroundColour, wx.Colour(255, 153, 51))
        else:
            wx.CallAfter(self.working_status_value.SetBackgroundColour, wx.Colour(0, 255, 0))
        wx.CallAfter(self.working_status_value.SetLabel, sap_response.working_status)
        wx.CallAfter(self.entries_value.SetLabel, sap_response.entries_number)

    def button_synchronize(self, event):
        threading.Thread(target=self.on_synchronize, args=(None,), daemon=True).start()

    def on_synchronize(self, event):
        self.update_button.Disable()
        self.update_button.SetLabel("Synchronizing...")
        self.update_button.Fit()
        if not self._update_running:
            self._update_running = True
            response = self.fetch_time_data()
            self.update_gui(response)
            self.update_button.Enable()
            self.update_button.SetLabel("Synchronize")
            self.update_button.Fit()
            self._update_running = False

    def on_exit(self, event):
        self.timer_visual_clock.Stop()
        self.timer_update_data.Stop()
        self.tray_icon.RemoveIcon()
        self.tray_icon.Destroy()
        self.Destroy()

    def minimize_to_tray(self):
        self.Hide()

    def restore_from_tray(self):
        self.Show()
        self.Raise()

    def on_close(self, event):
        self.minimize_to_tray()


class TimeTrackerTrayIcon(wx.adv.TaskBarIcon):
    def __init__(self, parent: TimeTrackerApp):
        super().__init__()
        self.parent = parent

        self.icon = wx.Icon(ICON_PATH)
        self.description_template = "Time Tracker - {working_status}\n\nTime Worked-> {worked_time}\nTime Left -> {left_time}\nOvertime -> {overtime}\nEntries -> {entries}"
        default_time = "00:00:00"
        self.SetIcon(
            self.icon,
            self.description_template.format(
                working_status=WorkingStatus.resting, worked_time=default_time, left_time=default_time, overtime=default_time, entries=0
            ),
        )

        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.on_restore)
        self.Bind(wx.adv.EVT_TASKBAR_RIGHT_UP, self.show_menu)

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.update_data, self.timer)
        self.timer.Start(1 * 1000)

    def CreatePopupMenu(self):
        menu = wx.Menu()
        restore_item = menu.Append(wx.ID_ANY, "Restore")
        sync_item = menu.Append(wx.ID_ANY, "Synchronize")
        view_source = menu.Append(wx.ID_ANY, "View Source")
        exit_item = menu.Append(wx.ID_EXIT, "Exit")

        self.Bind(wx.EVT_MENU, self.on_restore, restore_item)
        self.Bind(wx.EVT_MENU, self.on_sync, sync_item)
        self.Bind(wx.EVT_MENU, self.on_view_source, view_source)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        return menu

    def on_sync(self, event):
        wx.CallAfter(self.parent.on_synchronize, event)

    def on_view_source(self, event):
        webbrowser.open(PAGE_URL)

    def update_data(self, event):
        description = self.description_template.format(
            working_status=self.parent.working_status_value.GetLabel(),
            worked_time=self.parent.worked_time_value.GetLabel(),
            left_time=self.parent.left_time_value.GetLabel(),
            overtime=self.parent.overtime_value.GetLabel(),
            entries=self.parent.entries_value.GetLabel(),
        )
        wx.CallAfter(self.SetIcon, self.icon, description)

    def on_restore(self, event):
        self.parent.restore_from_tray()

    def on_exit(self, event):
        wx.CallAfter(self.Destroy)
        self.parent.on_exit(event)
        wx.CallAfter(wx.GetApp().ExitMainLoop)

    def show_menu(self, event):
        self.PopupMenu(self.CreatePopupMenu())


def main():
    multiprocessing.freeze_support()
    multiprocessing.set_executable(os.path.abspath(__file__))
    app = wx.App(False)

    checker = wx.SingleInstanceChecker()
    if checker.IsAnotherRunning():
        wx.MessageBox("Another instance of the app is already running.", "Error", wx.ICON_ERROR)
        return

    TimeTrackerApp(None)
    app.MainLoop()
    del app


if __name__ == "__main__":
    main()

# %%
