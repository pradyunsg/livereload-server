# livereload-server

A static file server that supports the LiveReload protocol.

## Command line interface

```sh-session
$ livereload-server --help
usage: livereload-server [-h] path_to_serve

positional arguments:
  path_to_serve  The path to watch and serve files from.

options:
  -h, --help     show this help message and exit

$ python -m livereload_server --help
usage: python -m livereload_server [-h] path_to_serve

positional arguments:
  path_to_serve  The path to watch and serve files from.

options:
  -h, --help     show this help message and exit
```

## API

This project has one public module (`livereload_server`) which exposes one public class (`livereload_server.LiveReloadingStaticServer`).

It can be used like:

```py
server = LiveReloadingStaticServer(path_to_serve, host="localhost", port=8000)
async with server:
    # The server is serving at the provided host and port.
    await asyncio.sleep(100)
    await server.reload("*")  # reload all paths
# The server is not serving at this point.
# It can be used in another `async with` block
```

### `livereload_server.LiveReloadingStaticServer`

An aiohttp static file server that implements the livereload protocol.

Methods:

- `__init__(path_to_serve: Path, *, host: str, port: int)`

  Initialize the server, providing information needed by the server to serve a directory.

- `async reload(path: str)`

  Reload the given path on all open windows. The [LiveReload protocol](https://web.archive.org/web/20210508192733/http://livereload.com/api/protocol/) documents this as:

  > as full as possible/known, absolute path preferred, file name only is OK

  It is not documented in the protocol but passing `*` reloads all paths on all open windows.

- `url: str` (readonly)

  URL that can be used to access the HTTP web server.

- `async __aenter__(...)`

  Start the live reloading server, in an `async with` block.

- `async __aexit__(...)`

  Stop the live reloading server, in an `async with` block.
