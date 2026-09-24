---
name: docstrings
description: >
  The docstring convention for this project - Google style with a single-line
  summary under 80 characters ending in a period, no description paragraph,
  Args entries without types, and Returns entries with them. Use this whenever
  writing, reviewing, or editing any Python docstring here: documenting new
  functions, classes and modules, backfilling docs on existing code, fixing
  ruff D-rule failures, or reviewing a diff that touches docstrings - even
  when the request is just "add docstrings" or "document this".
---

# Docstrings

Google style, trimmed in two places. The trimming is deliberate, so it helps to
know why before applying it.

`mypy` runs with `disallow_untyped_defs`, so every parameter is already
annotated in the signature. Repeating those types in `Args:` means two places
to update, and the docstring is the one that goes stale silently - nothing
checks it. So `Args:` drops the types and spends its words on what the
signature cannot say: units, valid ranges, ownership, what happens to the
value. `Returns:` keeps its type, because a reader scanning for what comes back
shouldn't have to jump to the signature to find out.

The second trim is the description paragraph. A docstring here is a summary
line and then sections - nothing in between.

## The shape

```python
def resize_image(image: Image, width: int, keep_ratio: bool = True) -> Image:
    """Resize an image to the given width.

    Args:
        image: Source image; left untouched.
        width: Target width in pixels.
        keep_ratio: Scale the height to preserve the aspect ratio.

    Returns:
        Image: A new image at the requested width.

    Raises:
        ValueError: If width is not positive.
    """
```

- **Summary: one line, under 80 characters, ends with a period.** Ruff's
  `line-length` is 88, so an 85-character summary passes the linter and still
  breaks this convention - the 80 is about staying readable in `git log`,
  `help()`, and side-by-side diffs, not about the linter.
- **No description paragraph.** If the summary cannot carry the idea, the
  context usually belongs in a comment at the point of complexity, where a
  reader hits the problem, rather than in a preamble everyone scrolls past. If
  it belongs nowhere, the function is doing too much.
- **Then `Args:`, `Returns:`, `Raises:`** in that order, each preceded by a
  blank line. Include only the sections that apply.

Most functions need none of the sections:

```python
def is_expired(token: Token) -> bool:
    """Check whether the token is past its expiry."""
```

Reach for sections when a caller could get it wrong - a unit that isn't
obvious, a mutation, an ordering guarantee, a raised error. A `Returns:` that
only restates the summary is worth deleting.

## Args

Name, colon, description. No parenthesised type.

```python
    Args:
        path: Path to the config file.
        retries: Attempts before giving up; 0 disables retrying.
```

Not `path (Path): ...` - the annotation already said `Path`.

Every parameter needs an entry or ruff `D417` fails the build; skip `self` and
`cls`. Wrap long descriptions with a hanging indent under the text.

Describe the constraint, not the type. `timeout: Seconds to wait; must be
positive.` earns its line. `timeout: The timeout.` does not - the name already
said that, and now it's one more line to keep in sync.

Entries can carry real weight when the parameter has real behaviour behind it:

```python
    Args:
        model: Model to train; moved to `config.device`.
        scheduler: Optional LR scheduler stepped once per epoch. If it is
            a `ReduceLROnPlateau`, `fit` must be called with a `val_loader`.
        metrics: Named metrics accumulated batch by batch alongside the loss,
            on both the training and validation pass. Each epoch's values land
            in `history` under "train_<name>"/"val_<name>".
```

Backticks around code references are the norm here - types, attributes, other
functions.

## Returns

Type, colon, description. Omit the section entirely for `-> None`.

```python
    Returns:
        Config: The parsed config, with environment overrides applied.
```

For a tuple, name what each slot is:

```python
    Returns:
        tuple[User, bool]: The user and whether this call created it.
```

## Raises

Exception class, colon, the condition - phrased as `If ...`, so the entry reads
as a trigger rather than a restatement of the class name.

```python
    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If the file is not valid TOML.
```

Document only what this function raises as part of its contract. An exception
that merely propagates from a callee isn't yours to promise.

## Modules and classes

Module docstrings are optional here (`D100` is ignored) but a one-liner saying
what the module is for is cheap and worth it.

A class docstring is a summary line and nothing else. Constructor parameters
are documented under `__init__`, where they sit next to the signature that
declares them - splitting them across an `Attributes:` block in the class
docstring just creates a second copy to maintain.

```python
class Trainer:
    """Full training pipeline for a supervised PyTorch model."""

    def __init__(self, model: nn.Module, config: TrainerConfig) -> None:
        """Initialize the trainer.

        Args:
            model: Model to train; moved to `config.device`.
            config: Trainer options; defaults to `TrainerConfig()`.
        """
```

## Checking the work

Ruff covers the mechanical half:

```sh
task ruff
```

The failures worth recognising: `D417` (a parameter with no entry), `D403`
(summary not capitalised), `D415` (summary lacks final punctuation), `D205`
(no blank line after the summary).

Three rules are outside what pydocstyle can express, so `scripts/` carries a
checker for them:

```sh
python .claude/skills/docstrings/scripts/check_docstrings.py src
```

| Code | What it catches |
| --- | --- |
| `DS001` | Summary line over 79 characters (indentation and quotes counted) |
| `DS002` | A description paragraph between the summary and the first section |
| `DS003` | A type in an `Args:` entry |

It is stdlib-only, takes files or directories (default `src`), exits 1 on any
finding, and accepts `--max-summary-length` if a project ever wants a different
limit. Run it after writing a batch of docstrings rather than reasoning about
character counts by hand.

`tests/**` ignores `D` entirely, so don't add docstrings there to satisfy a
linter that isn't looking - only where they help the next reader.
