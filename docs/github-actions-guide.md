# GitHub Actions guide

The backend workflow is:

```text
.github/workflows/update-index.yml
```

## Triggers

It runs:

- after a push to backend-relevant files on `main`;
- manually from the Actions tab;
- at 12:30 AM and 12:30 PM Japan time each day.

## Build and deployment sequence

1. Check out the repository.
2. Set up Python 3.13.
3. Install the locked requirements.
4. Run the full pytest suite.
5. Apply `config/search-policy.json` and collect enabled sources.
6. Generate the v3 search contract plus v2 compatibility surface.
7. Validate source counts, required endpoints, tokenization, file references, retired-output cleanup, and the 70 MiB artifact budget.
8. Upload `public/` as the GitHub Pages artifact.
9. Deploy the artifact to GitHub Pages.

## Reading failures

Common failures include:

- **Item count below 30,000:** required/open sources produced too few records; the workflow refuses to deploy an unexpectedly thin index.
- **Required source error or minimum failure:** inspect the adapter error in the build log and `data/source-health.json` from the last healthy deployment.
- **Artifact above 70 MiB:** compact documents or token postings exceeded the release budget.
- **Tokenization/contract test failure:** v3 no longer preserves required domain terms or its posting format changed without a frontend update.
- **Pages configuration failure:** confirm **Settings → Pages → Source** is set to **GitHub Actions**.

## Manual deployment check

After a successful run, verify:

```text
https://officialjivaro.github.io/backend-jtorrent/data/status.json
https://officialjivaro.github.io/backend-jtorrent/data/v3/manifest.json
```

`status.json` must advertise `search_contract: v3`. Deploy the frontend package only after that endpoint is live.

## Changing the schedule

The current workflow uses the `Asia/Tokyo` timezone:

```yaml
schedule:
  - cron: "30 0,12 * * *"
    timezone: "Asia/Tokyo"
```

## Scaling note

The workflow timeout is 60 minutes. If legitimate open-source/archive collection consistently needs longer, increase the timeout deliberately and review the Pages artifact budget at the same time. Do not solve collection growth by restoring duplicate legacy corpora.
