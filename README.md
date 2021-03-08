# smb1-proxy #

[![](https://img.shields.io/docker/v/andre74/smb1-proxy?sort=semver)](https://hub.docker.com/r/andre74/smb1-proxy/tags)
[![](https://img.shields.io/docker/pulls/andre74/smb1-proxy)](https://hub.docker.com/r/andre74/smb1-proxy)
[![](https://img.shields.io/docker/stars/andre74/smb1-proxy)](https://hub.docker.com/r/andre74/smb1-proxy)
[![](https://img.shields.io/docker/image-size/andre74/smb1-proxy)](https://hub.docker.com/r/andre74/smb1-proxy)
[![](https://img.shields.io/docker/cloud/build/andre74/smb1-proxy)](https://hub.docker.com/r/andre74/smb1-proxy/builds)

This container is used to proxy an existing secure smb share (version 2+) to allow legacy devices, that only support cifs/smb v1 the access to a specific share or folder on the secure share - without downgrading the complete server to smb v1. Its designed to forward all files to the secure share, without overwriting files on the destination

* GitHub: [Andreetje/smb1-proxy](https://github.com/Andreetje/smb1-proxy)
* Docker Hub: [andre74/smb1-proxy](https://hub.docker.com/repository/docker/andre74/smb1-proxy)

## Usage ##

Example docker-compose configuration:

```yml
version: '3.7'

services:
  smb1proxy:
    image: andre74/smb1-proxy
    environment:
      TZ: 'Europe/Berlin'
      USERID: 1000
      GROUPID: 1000
      SAMBA_USERNAME: scanuser
      SAMBA_PASSWORD: secret1
      GLOBAL: ntlm auth = yes
      PROXY1_ENABLE: 1
      PROXY1_SHARE_NAME: scanshare10
      PROXY1_REMOTE_PATH: //secure-host/share/path/to/folder
      PROXY1_REMOTE_DOMAIN: DOM
      PROXY1_REMOTE_USERNAME: UserA
      PROXY1_REMOTE_PASSWORD: password
      PROXY2_ENABLE: 1
      PROXY2_SHARE_NAME: scanshare30
      PROXY2_REMOTE_PATH: //other-host/share
      PROXY2_REMOTE_DOMAIN: DOM
      PROXY2_REMOTE_USERNAME: UserB
      PROXY2_REMOTE_PASSWORD: password
    ports:
      - "445:445/tcp"
    tmpfs:
      - /tmp
    restart: unless-stopped
    stdin_open: true
    tty: true
    privileged: true
```

## Config ##

The configuration is done via environment variables:

- `TZ`: Timezone
- `USERID`: Linux User ID
- `GROUPID`: Linux Group ID
- `SAMBA_USERNAME`: Global Username for the created shares
- `SAMBA_PASSWORD`: Global Password for the created shares
- `GLOBAL`: ntlm auth = yes ; Windows XP legacy support
- `PROXYx_ENABLE`: 0 = disabled, 1 = enabled
- `PROXYx_SHARE_NAME`: Samba Share name
- `PROXYx_REMOTE_PATH`: Can be just a share (//host/share) or a complete path (//host/share/path/to/folder)
- `PROXYx_REMOTE_DOMAIN`: Domain for remote path (optional)
- `PROXYx_REMOTE_USERNAME`: Username for remote path
- `PROXYx_REMOTE_PASSWORD`: Password for remote path

You can substitute x with an incremented number starting at 1 to create multiple entries. See example configuration.
