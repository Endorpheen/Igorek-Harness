"""Branded GTK installer using the existing verified installation controller."""
from __future__ import annotations

import contextlib
from pathlib import Path
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Gtk

STYLE = '''
window { background: #171b19; color: #edf2ed; }
.sidebar { background: #202623; padding: 32px; border-right: 1px solid #39443d; }
.content { padding: 36px; }
.title { font-size: 28px; font-weight: 700; color: #edf2ed; }
.brand { font-size: 24px; font-weight: 700; color: #c2ef78; }
.note { color: #abb6b0; font-size: 14px; }
label { font-size: 16px; }
button { border-radius: 12px; padding: 12px 22px; background: #29302c; color: #edf2ed; border: 1px solid #39443d; }
button.primary { background: #c2ef78; color: #1a2411; border-color: #c2ef78; font-weight: 700; }
button:hover { opacity: .85; }
checkbutton { padding: 14px; border-radius: 12px; background: #202623; }
.error { color: #f2a2a2; }
'''


def start(controller, base):
    Gtk.init()
    css = Gtk.CssProvider(); css.load_from_data(STYLE.encode())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    loop = GLib.MainLoop()

    class Dialogs(base):
        def __init__(self):
            self.finished = False
            self.pending = None
            self.window = Gtk.Window(title='Установка Игорька')
            self.window.set_default_size(880, 580)
            self.window.connect('close-request', self.close)
            self.root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            side.add_css_class('sidebar'); side.set_size_request(225, -1)
            logo = Gtk.Picture.new_for_filename(str((Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'packaging' else Path(__file__).parent) / 'igorek.svg'))
            logo.set_size_request(72, 72); logo.set_halign(Gtk.Align.START)
            side.append(logo)
            side.append(self.label('Игорёк', 'brand'))
            side.append(self.label('Агентная рабочая среда', 'note'))
            spacer = Gtk.Box(); spacer.set_vexpand(True); side.append(spacer)
            side.append(self.label('Ваш компьютер.\nВаши ключи.\nВаше рабочее пространство.', 'note'))
            self.mascot = Gtk.Picture.new_for_filename(str((Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'packaging' else Path(__file__).parent) / 'assets/evil-octopus.png'))
            self.mascot.set_size_request(180, 160)
            self.mascot.set_tooltip_text('Злобный Осьминог — Игорёк')
            side.append(self.mascot)
            self.root.append(side)
            self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            self.body.add_css_class('content'); self.body.set_hexpand(True)
            self.root.append(self.body); self.window.set_child(self.root)

        def label(self, text, css=None):
            label = Gtk.Label(label=text, wrap=True, xalign=0)
            label.set_max_width_chars(48)
            if css: label.add_css_class(css)
            return label

        def close(self, window):
            self.finished = True
            if self.pending: self.pending(None)
            self.window.set_visible(False)
            return True

        def invoke(self, callback):
            done = threading.Event(); result = {}
            def dispatch():
                try: result['value'] = callback()
                except Exception as error: result['error'] = error
                done.set(); return False
            GLib.idle_add(dispatch); done.wait()
            if 'error' in result: raise result['error']
            return result.get('value')

        def screen(self, title, text):
            self.window.set_default_widget(None)
            self.window.set_focus(None)
            child = self.body.get_first_child()
            while child:
                following = child.get_next_sibling(); self.body.remove(child); child = following
            self.mascot.set_visible(title in ('Установка Игорька', 'Готово'))
            self.body.append(self.label(title, 'title'))
            self.body.append(self.label(text, 'note'))
            self.window.present()

        def prompt(self, title, text, choices=None, confirm=False):
            if self.finished:
                return None
            done = threading.Event(); result = {}
            def show():
                if self.finished:
                    done.set()
                    return
                self.screen(title, text)
                selected = {}; group = None
                if choices:
                    for index, (key, label) in enumerate(choices):
                        item = Gtk.CheckButton(label=label)
                        if group: item.set_group(group)
                        else: group = item
                        item.connect('toggled', lambda widget, value=key: selected.update(value=value) if widget.get_active() else None)
                        item.set_active(index == 0); self.body.append(item)
                spacer = Gtk.Box(); spacer.set_vexpand(True); self.body.append(spacer)
                footer = Gtk.Box(spacing=12); footer.set_halign(Gtk.Align.END)
                def finish(value):
                    result['value'] = value; self.pending = None; done.set()
                self.pending = finish
                if choices or confirm:
                    cancel = Gtk.Button(label='Отмена'); cancel.connect('clicked', lambda *_: finish(None))
                    footer.append(cancel)
                button = Gtk.Button(label='Продолжить' if choices or confirm else 'Готово')
                button.add_css_class('primary')
                button.connect('clicked', lambda *_: finish(selected.get('value') if choices else True))
                footer.append(button); self.body.append(footer); self.window.set_default_widget(button)
            self.invoke(show); done.wait(); return result.get('value')

        def choose(self, text, choices):
            result = self.prompt('Установка Игорька', text, choices=choices)
            if result is None: self.finished = True
            return result

        def confirm(self, text):
            return bool(self.prompt('Подтвердите действие', text, confirm=True))

        def info(self, text):
            self.prompt('Готово', text)

        def error(self, text):
            self.prompt('Не удалось завершить', text)

        @contextlib.contextmanager
        def progress(self, text):
            def show():
                self.screen('Подготавливаю Игорька', text)
                spinner = Gtk.Spinner(); spinner.start(); spinner.set_halign(Gtk.Align.START)
                self.body.append(spinner)
            self.invoke(show)
            try: yield
            finally: self.invoke(lambda: None)

    dialogs = Dialogs(); result = {}
    def worker():
        try:
            while not dialogs.finished:
                result['code'] = controller(dialogs)
                if result['code']: break
        except Exception:
            result['code'] = 1
        finally:
            GLib.idle_add(loop.quit)
    threading.Thread(target=worker, name='installer-controller', daemon=True).start()
    loop.run(); dialogs.window.destroy()
    return result.get('code', 0)
