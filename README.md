# DEEPUTILS

Deeputils is a Python library that is adapated from [yt-dlp](https://github.com/yt-dlp/yt-dlp) that contians lot's neccessary utils and focus tool traversal.

## Installation

```bash
pip install deeputils
```

## Usage
We use yt-dlp usage see [yt-dlp usage](https://github.com/yt-dlp/yt-dlp/blob/master/CONTRIBUTING.md#yt-dlp-coding-conventions)

```python
import deeputils

html = EXRACTED_HTML

og_title = deeputils.og_search_title(html)
print(f'Video Title: {og_title}')

```

## License

[MIT](https://choosealicense.com/licenses/mit/)