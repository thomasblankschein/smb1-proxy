#!/usr/bin/env python3

import glob, os, shutil, time, datetime

print('File watcher started')

while True:
  i = 0
  while True:
    i = i + 1
    shareEnable = os.getenv('PROXY{}_ENABLE'.format(i))
    if shareEnable == None:
      break
    elif not shareEnable == "1":
      continue

    shareDirectory = '/share{}'.format(i)
    remoteMount = '/remote{}'.format(i)

    files = glob.glob(shareDirectory + '/*.*')
    for file in files:      
      _, filename = os.path.split(file)
      now = datetime.datetime.now()
      
      mtime = os.path.getmtime(file)
      if time.time() - mtime < 30:
        print(now," - Waiting for changes in File: '" + filename + "'")
        continue
        
      name, ext = os.path.splitext(filename) 
      mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")
      remotePath = remoteMount + "/" + mtime_str + ext
      
      try:
        print(now," - Move File: '" + file + "' -> '" + remotePath + "'")
        shutil.copyfile(file, remotePath)
        os.remove(file)
      except (FileNotFoundError, OSError) as err:
        print("↳ " + str(err))
  time.sleep(10)
