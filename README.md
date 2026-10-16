# dlp_utils

dlp_utils is a Python library that is adapated from [yt-dlp](https://github.com/yt-dlp/yt-dlp) Utils that contians daily Utilities.
## Installation

```bash
pip install dlp_utils
```

## Usage
We use yt-dlp usage see [yt-dlp usage](https://github.com/yt-dlp/yt-dlp/blob/master/CONTRIBUTING.md#yt-dlp-coding-conventions)

```python
import dlp_utils

html = EXRACTED_HTML

og_title = dlp_utils.og_search_title(html)
print(f'Video Title: {og_title}')

```
