# Autiobooks Automatically convert epubs to audiobooks
[![Installing via pip and running](https://github.com/plusuncold/autiobooks/actions/workflows/pip-install.yaml/badge.svg)](https://github.com/plusuncold/autiobooks/actions/workflows/pip-install.yaml)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/autiobooks)
![PyPI - Version](https://img.shields.io/pypi/v/autiobooks)

Autiobooks generates `.m4b` audiobooks from regular `.epub` e-books, using Kokoro's high-quality speech synthesis.

[Kokoro v0.19](https://huggingface.co/hexgrad/Kokoro-82M) is a recently published text-to-speech model with just 82M params and very natural sounding output.
It's released under Apache licence and it was trained on < 100 hours of audio.
It currently supports American, British English, French, Korean, Japanese and Mandarin, and a bunch of very good voices.

## How to install and run

If you have Python 3 on your computer, you can install it with pip.
Be aware that it won't work with Python 3.13.
Then you also need to download a couple of additional files in the same folder, which are about ~360MB:

```bash
pip install autiobooks
```

To start the program, run:

```bash
autiobooks
```

It will first create a bunch of `book_chapter_1.wav`, `book_chapter_2.wav`, etc. files in the same directory,
and at the end it will produce a `book.m4b` file with the whole book you can listen with VLC or any
 audiobook player.
It will only produce the `.m4b` file if you have `ffmpeg` installed on your machine.

## Author
by David Nesbitt, distributed under MIT license. Check out the excellent project [https://github.com/sanatic/audiblez](Audiblez) if you'd prefer a
command-line interface. This project uses some code from the project but will diverge.