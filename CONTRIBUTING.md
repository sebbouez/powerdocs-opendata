# Contributing

Thank you for contributing language data to PowerDocs!

PowerDocs downloads the languages straight from the `main` branch: once a pull request is merged,
it is available to every PowerDocs user. That is why every pull request is validated automatically,
then reviewed by a maintainer.

## Submitting a language

1. Fork the repository.
2. Add or update the language folder in `dictionaries/` and declare it in `index.json`
   (see [dictionaries/readme.md](dictionaries/readme.md) for the formats).
3. Update the list of files and run the validation locally (Python 3, no dependency needed):

   ```bash
   python3 .github/scripts/validate_languages.py --update-index --base origin/main
   ```

4. Open a pull request. The **Validate language data** check must pass before review.

Only submit data you are allowed to redistribute, and document its origin with `sourceUrl` and `license`
(or a `.md` note in the language folder).

## What the validation checks

**Index**
- `lcid` and `culture` are unique; `culture` is a culture tag such as `fr-FR`.
- The folder `dictionaries/<lcid>` exists.
- `version` is required (`1.0` or `1.0.0`).
- `files` lists an affix file and a dictionary, and only accepted file names (see the formats).
- `size` and `sha256` of each file match its content.
- `sourceUrl`, when present, is an `https://` address.

**Files**
- Size limits: 50 MB per file, 100 MB per language.
- No sub-folder in a language folder.
- No U+FFFD replacement character: it reveals a file damaged by a wrong encoding conversion.
- The `.aff` and `.dic` files can be decoded with the encoding declared by the `SET` directive of the `.aff` file.
- The `.dic` file starts with the approximate number of words.
- The `.dat` thesaurus declares its encoding on its first line and can be decoded with it.
- `language-rules.json` follows the expected schema and its `locale` is the culture of the language.
- `.md` notes are UTF-8 encoded.

Files present in a language folder but not declared in `index.json` are reported as warnings: PowerDocs never downloads them.

## Updating a language

Any change to a file of a language requires a new version: increase `version` in `index.json`.
PowerDocs offers the update to users who installed a previous version.

## Reviewing a pull request

The run summary of the **Validate language data** check lists, for each language, the files that were
added, modified or removed, the words added to or removed from the dictionary, and the changes of the style rules.
