from filewatcher import FileWatcherDaemon


fp = FileWatcherDaemon('./file_watch_debug.pid')
fp.debug = True
fp.run()
