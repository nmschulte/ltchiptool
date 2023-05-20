#  Copyright (c) Kuba Szczodrzyński 2023-1-2.

import sys
import threading
from logging import debug, info, warning
from os import rename, unlink
from os.path import dirname, isfile, join

import wx
import wx.adv
import wx.xrc
from click import get_app_dir

from ltchiptool.util.fileio import readjson, writejson
from ltchiptool.util.logging import LoggingHandler
from ltchiptool.util.lpm import LPM
from ltchiptool.util.lvm import LVM

from .panels.base import BasePanel
from .panels.log import LogPanel
from .utils import load_xrc_file, with_target


# noinspection PyPep8Naming
class MainFrame(wx.Frame):
    Panels: dict[str, BasePanel]
    init_params: dict

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        sys.excepthook = self.OnException
        threading.excepthook = self.OnException
        LoggingHandler.get().exception_hook = self.ShowExceptionMessage

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            xrc = join(sys._MEIPASS, "ltchiptool.xrc")
            icon = join(sys._MEIPASS, "ltchiptool.ico")
        else:
            xrc = join(dirname(__file__), "ltchiptool.xrc")
            icon = join(dirname(__file__), "ltchiptool.ico")

        self.Xrc = load_xrc_file(xrc)

        try:
            # try to find LT directory or local data snapshot
            LVM.get().require_version()
        except Exception as e:
            wx.MessageBox(message=str(e), caption="Error", style=wx.ICON_ERROR)
            wx.Exit()
            return

        old_config = join(get_app_dir("ltchiptool"), "config.json")
        self.config_file = join(get_app_dir("ltchiptool"), "gui.json")
        if isfile(old_config):
            # migrate old config to new filename
            if isfile(self.config_file):
                unlink(self.config_file)
            rename(old_config, self.config_file)
        self.loaded = False
        self.Panels = {}
        self.init_params = {}

        # initialize logging
        self.Log = LogPanel(parent=self, frame=self)
        self.Panels["log"] = self.Log
        # main window layout
        self.Notebook = wx.Notebook(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.Notebook, flag=wx.EXPAND)
        sizer.Add(self.Log, proportion=1, flag=wx.EXPAND)
        self.SetSizer(sizer)

        # list all built-in panels
        from .panels.about import AboutPanel
        from .panels.flash import FlashPanel

        windows = [
            ("flash", FlashPanel),
            ("about", AboutPanel),
        ]

        # load all panels from plugins
        lpm = LPM.get()
        for name, plugin in lpm.plugins.items():
            if not plugin or not plugin.has_gui:
                continue
            for gui_name, cls in plugin.build_gui().items():
                windows.append((f"plugin.{name}.{gui_name}", cls))

        # dummy name for exception messages
        name = "UI"
        try:
            self.SetMenuBar(self.Xrc.LoadMenuBar("MainMenuBar"))

            for name, cls in windows:
                if name.startswith("plugin."):
                    # mark as loaded after trying to build any plugin
                    self.loaded = True
                if issubclass(cls, BasePanel):
                    panel = cls(parent=self.Notebook, frame=self)
                    self.Panels[name] = panel
                else:
                    warning(f"Unknown GUI element: {cls}")

            self.loaded = True
        except Exception as e:
            LoggingHandler.get().emit_exception(e, msg=f"Couldn't build {name}")
            if not self.loaded:
                self.OnClose()

        self.Bind(wx.EVT_SHOW, self.OnShow)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_MENU, self.OnMenu)

        self.SetSize((700, 800))
        self.SetMinSize((600, 700))
        self.SetIcon(wx.Icon(icon, wx.BITMAP_TYPE_ICO))
        self.CreateStatusBar()

    @property
    def _settings(self) -> dict:
        return readjson(self.config_file) or {}

    @_settings.setter
    def _settings(self, value: dict):
        writejson(self.config_file, value)

    # noinspection PyPropertyAccess
    def GetSettings(self) -> dict:
        pos: wx.Point = self.GetPosition()
        size: wx.Size = self.GetSize()
        page: str = self.NotebookPageName
        return dict(
            pos=[pos.x, pos.y],
            size=[size.x, size.y],
            page=page,
        )

    def SetSettings(
        self,
        pos: tuple[int, int] = None,
        size: tuple[int, int] = None,
        page: str = None,
        **_,
    ):
        if pos:
            self.SetPosition(pos)
        if size:
            self.SetSize(size)
        if page is not None:
            self.NotebookPageName = page

    @property
    def NotebookPagePanel(self) -> wx.Panel:
        return self.Notebook.GetCurrentPage()

    @NotebookPagePanel.setter
    def NotebookPagePanel(self, panel: wx.Panel):
        for i in range(self.Notebook.GetPageCount()):
            if panel == self.Notebook.GetPage(i):
                self.Notebook.SetSelection(i)
                return

    @property
    def NotebookPageName(self) -> str:
        for name, panel in self.Panels.items():
            if panel == self.Notebook.GetCurrentPage():
                return name

    @NotebookPageName.setter
    def NotebookPageName(self, name: str):
        panel = self.Panels.get(name, None)
        if panel:
            self.NotebookPagePanel = panel

    @staticmethod
    def OnException(*args):
        if isinstance(args[0], type):
            LoggingHandler.get().emit_exception(args[1])
        else:
            LoggingHandler.get().emit_exception(args[0].exc_value)

    @staticmethod
    def ShowExceptionMessage(e, msg):
        text = f"{type(e).__name__}: {e}"
        wx.MessageBox(
            message=f"{msg}\n\n{text}" if msg else text,
            caption="Error",
            style=wx.ICON_ERROR,
        )

    def OnShow(self, *_):
        settings = self._settings
        self.SetSettings(**settings.get("main", {}))
        for name, panel in self.Panels.items():
            panel.SetSettings(**settings.get(name, {}))
            panel.SetInitParams(**self.init_params)
        if settings:
            info(f"Loaded settings from {self.config_file}")
        for name, panel in self.Panels.items():
            panel.OnShow()

    def OnClose(self, *_):
        if not self.loaded:
            # avoid writing partial settings in case of loading failure
            self.Destroy()
            return
        settings = self._settings
        settings["main"] = self.GetSettings()
        for name, panel in self.Panels.items():
            panel.OnClose()
            settings[name] = panel.GetSettings() or {}
        self._settings = settings
        info(f"Saved settings to {self.config_file}")
        self.Destroy()

    @with_target
    def OnMenu(self, event: wx.CommandEvent, target: wx.Menu):
        if not isinstance(target, wx.Menu):
            # apparently EVT_MENU fires on certain key-presses too
            return
        item: wx.MenuItem = target.FindItemById(event.GetId())
        if not item:
            return
        title = target.GetTitle()
        label = item.GetItemLabel()
        checked = item.IsChecked() if item.IsCheckable() else False

        match (title, label):
            case ("File", "Quit"):
                self.Close(True)

            case ("Debug", "Print settings"):
                debug(f"Main settings: {self.GetSettings()}")
                for name, panel in self.Panels.items():
                    debug(f"Panel '{name}' settings: {panel.GetSettings()}")

            case _:
                for panel in self.Panels.values():
                    panel.OnMenu(title, label, checked)
