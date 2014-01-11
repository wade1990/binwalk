import os
import sys
import imp
import inspect
import binwalk.core.common
import binwalk.core.settings
from binwalk.core.compat import *

class Plugin(object):
    '''
    Class from which all plugin classes are based.
    '''
    # A list of case-sensitive module names for which this plugin should be loaded.
    # If no module names are specified, the plugin will be loaded for all modules.
    MODULES = []

    def __init__(self, module):
        '''
        Class constructor.

        @module - A handle to the current module that this plugin is loaded for.

        Returns None.
        '''
        self.module = module
        
        if not self.MODULES or self.module.name in self.MODULES:
            self._enabled = True
            self.init()
        else:
            self._enabled = False

    def init(self):
        '''
        Child class should override this if needed.
        Invoked during plugin initialization.
        '''
        pass

    def pre_scan(self):
        '''
        Child class should override this if needed.
        '''
        pass

    def scan(self, result):
        '''
        Child class should override this if needed.
        '''
        pass

    def post_scan(self):
        '''
        Child class should override this if needed.
        '''
        pass

class Plugins(object):
    '''
    Class to load and call plugin callback functions, handled automatically by Binwalk.scan / Binwalk.single_scan.
    An instance of this class is available during a scan via the Binwalk.plugins object.

    Each plugin must be placed in the user or system plugins directories, and must define a class named 'Plugin'.
    The Plugin class constructor (__init__) is passed one argument, which is the current instance of the Binwalk class.
    The Plugin class constructor is called once prior to scanning a file or set of files.
    The Plugin class destructor (__del__) is called once after scanning all files.

    The Plugin class can define one or all of the following callback methods:

        o pre_scan(self, fd)
          This method is called prior to running a scan against a file. It is passed the file object of
          the file about to be scanned.

        o pre_parser(self, result)
          This method is called every time any result - valid or invalid - is found in the file being scanned.
          It is passed a dictionary with one key ('description'), which contains the raw string returned by libmagic.
          The contents of this dictionary key may be modified as necessary by the plugin.

        o callback(self, results)
          This method is called every time a valid result is found in the file being scanned. It is passed a 
          dictionary of results. This dictionary is identical to that passed to Binwalk.single_scan's callback 
          function, and its contents may be modified as necessary by the plugin.

        o post_scan(self, fd)
          This method is called after running a scan against a file, but before the file has been closed.
          It is passed the file object of the scanned file.

    Values returned by pre_scan affect all results during the scan of that particular file.
    Values returned by callback affect only that specific scan result.
    Values returned by post_scan are ignored since the scan of that file has already been completed.

    By default, all plugins are loaded during binwalk signature scans. Plugins that wish to be disabled by 
    default may create a class variable named 'ENABLED' and set it to False. If ENABLED is set to False, the
    plugin will only be loaded if it is explicitly named in the plugins whitelist.
    '''

    SCAN = 'scan'
    PRESCAN = 'pre_scan'
    POSTSCAN = 'post_scan'
    MODULE_EXTENSION = '.py'

    def __init__(self, parent=None):
        self.scan = []
        self.pre_scan = []
        self.post_scan = []
        self.parent = parent
        self.settings = binwalk.core.settings.Settings()

    def __del__(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, t, v, traceback):
        pass

    def _call_plugins(self, callback_list, arg):
        for callback in callback_list:
            try:
                if arg:
                    callback(arg)
                else:
                    callback()
            except KeyboardInterrupt as e:
                raise e
            except Exception as e:
                binwalk.common.core.warning("%s.%s failed: %s" % (callback.__module__, callback.__name__, e))

    def _find_plugin_class(self, plugin):
        for (name, klass) in inspect.getmembers(plugin, inspect.isclass):
            if issubclass(klass, Plugin) and klass != Plugin:
                return klass
        raise Exception("Failed to locate Plugin class in " + plugin)

    def list_plugins(self):
        '''
        Obtain a list of all user and system plugin modules.

        Returns a dictionary of:

            {
                'user'        : {
                            'modules'     : [list, of, module, names],
                            'descriptions'    : {'module_name' : 'module pydoc string'},
                            'enabled'       : {'module_name' : True},
                            'path'        : "path/to/module/plugin/directory"
                },
                'system'    : {
                            'modules'     : [list, of, module, names],
                            'descriptions'    : {'module_name' : 'module pydoc string'},
                            'enabled'       : {'module_name' : True},
                            'path'        : "path/to/module/plugin/directory"
                }
            }
        '''

        plugins = {
            'user'   : {
                    'modules'       : [],
                    'descriptions'  : {},
                    'enabled'       : {},
                    'path'          : None,
            },
            'system' : {
                    'modules'       : [],
                    'descriptions'  : {},
                    'enabled'       : {},
                    'path'          : None,
            }
        }

        for key in plugins.keys():
            plugins[key]['path'] = self.settings.get_file_path(key, self.settings.PLUGINS)

            if plugins[key]['path']:
                for file_name in os.listdir(plugins[key]['path']):
                    if file_name.endswith(self.MODULE_EXTENSION):
                        module = file_name[:-len(self.MODULE_EXTENSION)]
                    
                        try: 
                            plugin = imp.load_source(module, os.path.join(plugins[key]['path'], file_name))
                            plugin_class = self._find_plugin_class(plugin)

                            plugins[key]['enabled'][module] = True
                            plugins[key]['modules'].append(module)
                        except KeyboardInterrupt as e:
                            raise e
                        except Exception as e:
                            binwalk.common.core.warning("Error loading plugin '%s': %s" % (file_name, str(e)))
                            plugins[key]['enabled'][module] = False
                        
                        try:
                            plugins[key]['descriptions'][module] = plugin_class.__doc__.strip().split('\n')[0]
                        except KeyboardInterrupt as e:
                            raise e
                        except Exception as e:
                            plugins[key]['descriptions'][module] = 'No description'
        return plugins

    def load_plugins(self):
        plugins = self.list_plugins()
        self._load_plugin_modules(plugins['user'])
        self._load_plugin_modules(plugins['system'])

    def _load_plugin_modules(self, plugins):
        for module in plugins['modules']:
            try:
                file_path = os.path.join(plugins['path'], module + self.MODULE_EXTENSION)
            except KeyboardInterrupt as e:
                raise e
            except Exception:
                continue

            try:
                plugin = imp.load_source(module, file_path)
                plugin_class = self._find_plugin_class(plugin)

                class_instance = plugin_class(self.parent)
                if not class_instance._enabled:
                    continue

                try:
                    self.scan.append(getattr(class_instance, self.SCAN))
                except KeyboardInterrupt as e:
                    raise e
                except Exception as e:
                    pass

                try:
                    self.pre_scan.append(getattr(class_instance, self.PRESCAN))
                except KeyboardInterrupt as e:
                    raise e
                except Exception as e:
                    pass

                try:
                    self.post_scan.append(getattr(class_instance, self.POSTSCAN))
                except KeyboardInterrupt as e:
                    raise e
                except Exception as e:
                    pass
                            
            except KeyboardInterrupt as e:
                raise e
            except Exception as e:
                binwalk.common.core.warning("Failed to load plugin module '%s': %s" % (module, str(e)))

    def pre_scan_callbacks(self, obj):
        return self._call_plugins(self.pre_scan, None)

    def post_scan_callbacks(self, obj):
        return self._call_plugins(self.post_scan, None)

    def scan_callbacks(self, obj):
        return self._call_plugins(self.scan, obj)

