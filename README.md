# Shorts to TikTok

A small local app that copies your own YouTube Shorts onto your TikTok account.
It runs on your machine and talks to Google and TikTok directly. No third-party
service ever sees your videos or your keys.

## Running it

Double-click `run.bat`. The first run builds a virtual environment and installs
two packages, then a browser tab opens at `http://127.0.0.1:8712/`.

## The two sets of keys

Every field in the app has a **?** button next to it with the direct links and
the steps. In short:

**YouTube** needs one API key. Make a project in the Google Cloud console,
enable YouTube Data API v3, create an API key under Credentials, paste it in.
This key reads public channel data only.

**TikTok** needs a client key and a client secret from an app you create at
`developers.tiktok.com/apps`. Add the Login Kit and Content Posting API
products, create a Sandbox and put your own TikTok account in it, then register
this redirect URI exactly:

```
http://127.0.0.1:8713/callback
```

Register it under Login Kit's **Desktop** tab. The Web tab only accepts real
https domains and rejects loopback addresses. Desktop accepts plain http on
localhost or 127.0.0.1, which is why there is no certificate to deal with. The
desktop flow requires PKCE, and TikTok wants the challenge hex encoded rather
than the usual base64url, which the app handles.

Ignore the **Verify domains** button. Domain verification exists for the pull
by URL transfer mode, where TikTok fetches your video off a public address.
This app pushes the file bytes directly, so nothing is ever hosted.

If the browser does not come back on its own, the Connect panel has a box where
you paste the redirect URL out of the address bar instead.

## Two ways videos land

**Inbox** is the default. It needs only the `video.upload` scope and works
inside a TikTok sandbox, so no app review.

To find what it uploaded, open the TikTok **mobile app**, tap **Inbox** in the
bottom row, open **System notifications**, and look for the message saying your
content is ready. That opens the normal editor, where you write the caption and
post. Inbox uploads are not posts yet, so they do not appear in TikTok Studio,
on the web, or on your profile until you finish them there.

TikTok only holds about five unposted uploads at a time. Past that it answers
`spam_risk_too_many_pending_share`. The app treats that as a queue signal
rather than a failure: it holds position, backs off, and retries, so a large
selection drips in as you clear the earlier ones from your phone. The queue is
saved to disk, so closing the app and reopening it resumes where it stopped.

**Direct** posts to your profile without touching the phone. This needs the
`video.publish` scope. Until TikTok audits your developer app, everything it
posts is forced to private visibility, so treat direct mode as useful once your
app is approved.

## How each video moves

The YouTube Data API lists your channel and its metadata, but Google offers no
API that hands over the video file itself. So the media comes down through
`yt-dlp`, which is what makes this work at all. Then the file is pushed to
TikTok's Content Posting API and the app polls until TikTok reports the upload
finished.

Downloaded files stay in `downloads/` and are reused, so re-porting a video
does not re-download it.

## Things worth knowing

- Captions are built from the YouTube title plus any hashtags in the
  description. Edit the template in the left rail.
- Videos you have already ported are remembered in `config.json` and come back
  unchecked so you do not post twice.
- Music that was licensed for YouTube does not carry over. TikTok may mute or
  block those videos on its own.
- `config.json` holds your keys and TikTok tokens in plain text. It never leaves
  the machine, but do not commit it anywhere.
