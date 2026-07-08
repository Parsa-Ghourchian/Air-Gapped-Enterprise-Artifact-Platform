# Python 2 Legacy Support

Python 2 support is isolated for legacy systems only.

Repository model:

- pypi2-hosted
- pypi2-proxy
- pypi2-group

Bundle layout:

- python2/requirements.txt
- python2/wheels/
- manifest.txt
- SHA256SUMS

Build:

    make pypi2-repos
    make python2-bundle
    make python2-bundle-test

Install on target:

    python2 -m pip install --no-index --find-links /path/to/python2/wheels -r /path/to/python2/requirements.txt
