# Bellows Games social poster

Posts to the **Bellows Games.** Facebook Page and the linked Instagram account
**@bellows30**, automatically and for free.

- **Claude** writes the posts each week and adds them to `queue/` (one file per post),
  with images in `images/`.
- **GitHub Actions** checks the queue every hour and publishes anything that is due,
  then writes the result back into the post's file, so nothing is posted twice.

## How a post looks

One JSON file per post in `queue/`, e.g. `queue/2026-10-27-new-level.json`:

```json
{
  "publish_at": "2026-10-27T18:00:00+01:00",
  "platforms": ["instagram", "facebook"],
  "caption": "New level just dropped! ...",
  "image": "images/2026-10-27-new-level.jpg"
}
```

Rules:
- `publish_at` must include the timezone (`+01:00` for Danish winter time, `+02:00` in summer).
- Instagram needs an image (a **.jpg**) or a `"video"` (an **.mp4**, 9:16, 3-90 s).
  A video becomes an Instagram Reel (also shown in the feed) and a Facebook Page video.
- Facebook can post text only, or text with an image.

## One-time setup

### 1. GitHub
1. Create a free GitHub account if you don't have one.
2. Create a new **public** repository (for example `bellows-social`) and upload these files.
   It has to be public so Meta can fetch the images.
3. Under **Settings → Actions → General**, make sure Actions are allowed and that
   "Workflow permissions" is set to **Read and write**.

### 2. Meta developer app and token
Menu names on Meta's site change from time to time; look for the closest match.

1. Go to developers.facebook.com, log in with the Facebook account that runs the
   Bellows Games. Page, and create a new app of the **Business** type.
2. Open the **Graph API Explorer** (Tools menu), pick your app, and generate a
   **User access token** with these permissions:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`, `business_management`.
3. Open the **Access Token Debugger**, paste that token, and click
   **Extend Access Token** to get a long-lived token.
4. Back in the Graph API Explorer, use the long-lived token and request `me/accounts`.
   The `access_token` shown next to **Bellows Games.** is a Page token that does not expire.
5. In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Name it `META_PAGE_TOKEN` and paste the Page token.

Keep the token private: never paste it into a post, an issue or a file in the repo.

### 3. Test it
In the repo, open **Actions → Publish social posts → Run workflow**, choose `check`,
and run it. The log should show the Page name and `@bellows30`.

## Good to know
- GitHub pauses scheduled workflows in a repo with no activity for 60 days. The weekly
  posts Claude adds count as activity, so this only matters if posting stops.
- If a post fails three times on a platform, it is skipped and the error is saved
  in its file under `errors`. A failed run also triggers GitHub's email notification.
- Optional: set a repository variable `GRAPH_VERSION` to change the Meta API version.
