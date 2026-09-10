# What to paste into the TikTok app registration form

Field names vary a little as TikTok changes the console. Match on meaning, not
on exact wording.

## App icon

Upload `assets/icon.png` from this repo. It is 512 by 512 with a transparent
corner radius, which is what the console expects.

## App name

```
Shorts Porter
```

Do not put "TikTok" in the name. Platforms routinely reject app names that
contain their own trademark, and it is a slow way to find that out.

## Category

Pick **Productivity** if it is offered. **Tools** or **Utilities** are fine
substitutes. Nothing downstream depends on this.

## Short description

```
A personal tool that copies my own YouTube Shorts onto my own TikTok account.
```

## Detailed description

```
Shorts Porter is a single-user tool I run on my own computer to move my own
short-form videos between the two platforms I publish on.

It reads the list of Shorts on my YouTube channel through the YouTube Data
API, downloads the ones I select, and uploads them to my own TikTok account
using the Content Posting API. Uploads go to my creator inbox with the
video.upload scope, so I review each one and write the final caption inside
the TikTok app before anything is published.

The tool runs entirely on my local machine. It has no server, no other users,
and no third party in the request path. It stores my credentials in a local
file on my own disk and sends video only to TikTok.
```

## Platforms

Tick **Desktop**. This is the one that matters. The Desktop checkbox is what
reveals the Desktop redirect URI tab, and that tab is the only place TikTok
will accept a loopback address like `http://127.0.0.1:8713/callback`. The Web
tab requires a real https domain and will reject it every time.

You can leave Web ticked as well, but Desktop must be on.

## The three URL fields

TikTok requires a website, a terms of service page, and a privacy policy page,
and it will not save the app without all three. They have to be live pages, not
files in a repository.

The `site/` folder here is a complete four page site that satisfies all three,
plus a callback page for sign-in. Put it on any static host you control. If you
use Cloudflare Pages:

```bash
npx wrangler pages project create shorts-porter --production-branch main
npx wrangler pages deploy site --project-name shorts-porter --branch main
```

That alone gives you a working `*.pages.dev` address you can paste straight
into the form. Attach your own domain afterwards if you want a nicer one.

Fill the fields with, where `<your-site>` is whatever host you landed on:

| Field | Value |
| --- | --- |
| Web/Desktop URL | `https://<your-site>/` |
| Terms of Service URL | `https://<your-site>/terms` |
| Privacy Policy URL | `https://<your-site>/privacy` |

Read `site/privacy.html` and `site/terms.html` before you publish them. They
describe a single-user local tool that collects nothing, which is true of this
software as written. If you change what the tool does, change those pages too.

## Redirect URI

Try the loopback address first, on the Desktop tab:

```
http://127.0.0.1:8713/callback
```

Some accounts get `localhost is not supported` from the console. If that
happens, register the callback page on your own site instead:

```
https://<your-site>/callback
```

Then put that same string in the Redirect URI box in the app. TikTok will send
you to that page with the code in the address bar, and the page gives you a
copy button. Paste it into the box under Connect TikTok and sign-in completes.

`config.json` is in `.gitignore`, so pushing this repo will not publish your
keys. Verify that before the first push.

## What to leave alone

**Direct Post** can stay off. Inbox uploads are enabled by default and are all
this tool uses.

**Verify domains** is not for you. It exists for the pull by URL transfer mode,
where TikTok fetches a video from an address you own. This tool pushes the file
bytes directly.
