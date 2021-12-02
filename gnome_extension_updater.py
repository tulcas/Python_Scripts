#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import re

import requests
from gi.repository import GLib, Gio

SHELL_SCHEMA = "org.gnome.shell"
ENABLED_EXTENSIONS_KEY = "enabled-extensions"
EXTENSION_DISABLE_VERSION_CHECK_KEY = "disable-extension-version-validation"
DISABLE_USER_EXTENSIONS_KEY = "disable-user-extensions"


def get_proxy(url):
    proxies = Gio.ProxyResolver.get_default().lookup(url)
    if proxies is not None:
        proxy = proxies[0]
        if proxy.startswith('direct'):
            proxies = None
        else:
            proxies = {}
            for scheme in ('http', 'https'):
                proxies[scheme] = proxy
    return proxies


# https://wiki.gnome.org/Projects/GnomeShell/Extensions/UUIDGuidelines
def is_uuid(uuid):
    return uuid is not None and re.match('[-a-zA-Z0-9@._]+$', uuid) is not None


class GNOMEShellExtensionUpdater(object):
    def __init__(self):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.shell_proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            'org.gnome.Shell',
            '/org/gnome/Shell',
            'org.gnome.Shell.Extensions',
            None
        )
        self.gio_settings = Gio.Settings.new(SHELL_SCHEMA)

    def dbus_call_response(self, method, parameters):
        return self.shell_proxy.call_sync(
            method,
            parameters,
            Gio.DBusCallFlags.NONE,
            -1,
            None
        )

    def listExtensions(self):
        return self.dbus_call_response("ListExtensions", None).unpack()[0]

    def listEnabledExtensions(self):
        return self.gio_settings.get_strv(ENABLED_EXTENSIONS_KEY)

    def setEnabledExtensions(self, extensions):
        return self.gio_settings.set_strv(ENABLED_EXTENSIONS_KEY, extensions)

    def installExtension(self, uuid):
        return self.dbus_call_response("InstallRemoteExtension", GLib.Variant.new_tuple(GLib.Variant.new_string(uuid)))

    def getExtensionErrors(self, uuid):
        return self.dbus_call_response("GetExtensionErrors", GLib.Variant.new_tuple(GLib.Variant.new_string(uuid)), )

    def getExtensionInfo(self, uuid):
        return self.dbus_call_response("GetExtensionInfo", GLib.Variant.new_tuple(GLib.Variant.new_string(uuid)))

    def uninstallExtension(self, uuid):
        return self.dbus_call_response("UninstallExtension", GLib.Variant.new_tuple(GLib.Variant.new_string(uuid)))

    def getUserExtensionsDisabled(self):
        return self.gio_settings.get_boolean(DISABLE_USER_EXTENSIONS_KEY)

    def getVersionValidationDisabled(self):
        return self.gio_settings.get_boolean(EXTENSION_DISABLE_VERSION_CHECK_KEY)

    def setUserExtensionsDisabled(self, disable):
        return self.gio_settings.set_boolean(DISABLE_USER_EXTENSIONS_KEY, disable)

    def setVersionValidationDisabled(self, disable):
        return self.gio_settings.set_boolean(EXTENSION_DISABLE_VERSION_CHECK_KEY, disable)

    def check_update(self, update_url='https://extensions.gnome.org/update-info/', enabled_only=True):
        extensions = self.listExtensions()
        enabled_extensions = self.listEnabledExtensions() if enabled_only else []

        installed = {}

        for uuid in extensions:
            # gnome-shell/js/misc/extensionUtils.js
            # EXTENSION_TYPE.PER_USER = 2
            if is_uuid(uuid) and extensions[uuid]['type'] == 2 and (not enabled_only or uuid in enabled_extensions):
                try:
                    installed[uuid] = {
                        'version': int(extensions[uuid]['version'])
                    }
                except (ValueError, KeyError):
                    installed[uuid] = {
                        'version': 1
                    }

        with requests.session() as http:
            response = http.get(
                update_url,
                # json=installed,  # POST fails due to CSRF protection
                params={
                    'shell_version': self.shell_proxy.get_cached_property("ShellVersion").unpack(),
                    'installed': json.dumps(installed)
                },
                proxies=get_proxy(update_url),
                timeout=5
            )
            response.raise_for_status()
            return extensions, response.json()

    def do_update(self):
        user_extensions_disabled = self.getUserExtensionsDisabled()
        enabled_extensions = self.listEnabledExtensions()
        print(user_extensions_disabled, enabled_extensions)
        extensions, updates = self.check_update(enabled_only=False)
        print(json.dumps(extensions, indent="  "))
        print(json.dumps(updates, indent="  "))

        if 'upgrade' in updates.values():
            self.setUserExtensionsDisabled(True)
            for extension, action in updates.items():
                if action == 'upgrade':
                    self.uninstallExtension(extension)
                    self.installExtension(extension)

            self.setEnabledExtensions(enabled_extensions)
            self.setUserExtensionsDisabled(user_extensions_disabled)
        else:
            print("Nothing to do")


def backup_extensions():
    import os, appdirs, sh

    APPNAME = "gnome-extension-updater"
    datadir = appdirs.user_data_dir(APPNAME)
    os.makedirs(datadir, exist_ok=True)
    sh.tar("-caf",
           os.path.join(datadir, "pre-update-extensions.tar.gz"),
           os.path.expanduser("~/.local/share/gnome-shell/extensions"))
    sh.dconf.dump("/", _out=os.path.join(datadir, "pre-update.dconf"))


if __name__ == "__main__":
    backup_extensions()
    GNOMEShellExtensionUpdater().do_update()
