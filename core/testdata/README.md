# `core/testdata/`

Fixed example data used only by tests — nothing here is read by the measurement pipeline itself
while it's actually running.

## What you'll see here

One subfolder: `vectors/`. See its own README for what's inside.

## Why it needs to exist

`core/measure/` is supposed to be frozen — stable, unlikely to change. The only way to actually
trust that is to have a set of inputs with known, hand-checked correct outputs, and re-run them
after every change. That's what `vectors/` is for. This folder is the fixture library that backs
`core/measure/verify.py`.
