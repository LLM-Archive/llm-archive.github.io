# How we prove we decided first

**In one sentence:** before we run a new protocol, we fix its fingerprint in a place nobody can
edit, so that nobody — including us — can claim afterwards that we chose the protocol after seeing
the result.

## Why this is needed

Imagine a teacher who writes the exam questions *after* seeing which ones the student can answer.
The exam would look impressive and prove nothing.

A benchmark has the same weak spot. If we could quietly change a protocol after seeing how a model
did, the numbers would look good but mean little. The dates on our own files don't help: we write
them ourselves, so we could write any date we liked.

So we need a clock that **we don't control**.

## The idea, in plain words

Every file has a **fingerprint**: a short code that changes completely if even one letter of the
file changes (the `panel_sha256` and `protocol_sha256` you see in the data are fingerprints of
this kind; see [`glossary.md`](glossary.md)). We collect the fingerprints of our protocols in one
small file, the **manifest**, and send only the manifest's own fingerprint (never the protocols
themselves) to a free public service called [OpenTimestamps](https://opentimestamps.org).
It writes the code into the Bitcoin record, which is public and which nobody can rewrite.

Later, anyone can check: *"this manifest already existed on this date."* Not "exactly when
we made it", only "no later than". That is the direction that matters: we can't claim to have
decided earlier than we really did.

## How we do it: four steps

1. **Write the manifest.** A small file listing the fingerprints of every protocol that is ready.
2. **Stamp it.** One command sends the manifest's fingerprint to the free service. This takes seconds.
3. **Wait a few hours.** The service is grouping many stamps into one Bitcoin entry.
4. **Finish the stamp.** One more command collects the finished proof and saves it next to the
   manifest. From now on, the proof stands on its own.

We run a new protocol for the first time only **after step 2**. Steps 3 and 4 finish the proof
afterwards; the stamp already fixes the date the service received it.

The first manifest was stamped on **24 September 2026**, before the `v1` protocols were run. The
`v0` protocols had already been run by then, so for those the stamp shows only that they existed by
that date, not that they were fixed before their first run.

## What you can check yourself

Today, not yet: the manifest also contains fingerprints of protocols we keep private on purpose
(the sealed lane, see [`glossary.md`](glossary.md)), so we haven't published it. The fingerprints
of the public protocols are already published in `stability.csv`, so once the manifest is shown —
in part or in full — anyone can match it against what has been public all along, and check the stamp with the free
`ots verify` tool.

## What this is not

It is one cheap, outside check on one thing: *when a protocol was fixed*. It doesn't prove the results
weren't changed later, and it doesn't replace our other checks. We list wider ideas as future work
rather than pretend they exist.
