# Runtime Directory

This repository intentionally does not commit the bundled runtime because it is large and platform-specific.

For local Windows runs, provide one of these:

1. Set `MH_AGENT_RUNTIME_DIR` to a directory with this layout:

   ```text
   runtime/
     python/python.exe
     node/
     git/
     texlive/
     draw.io/
   ```

2. Copy the original packaged `runtime` directory next to this file.
3. Use a local Windows 64-bit Python 3.11 with the packages in `app/backend/requirements.txt`.

The compiled backend extensions are built for CPython 3.11 on Windows x64.
