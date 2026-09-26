The language data offered in PowerDocs, one folder per language.

## Adding a language

1. Create a folder named after the LCID of the language (see [lcid.md](../lcid.md)), for example `1036` for French (France).
2. Put the language files in this folder. Their names are built from the culture tag, with an underscore (`fr-FR` → `fr_FR`):

| File | Required | Description |
|---|---|---|
| `fr_FR.aff` | yes | Hunspell affix file. Its `SET` directive declares the encoding of both `.aff` and `.dic` files. |
| `fr_FR.dic` | yes | Hunspell dictionary: the approximate number of words on the first line, then one word per line. |
| `fr_FR.dat` | no | MyThes thesaurus: the encoding on the first line, then the entries. |
| `language-rules.json` | no | Style rules used by the PowerDocs editor tips (see below). |
| `*.md` | no | Notes, such as the license of the dictionary. |

No other file and no sub-folder is accepted. `fr_FR.exclude.txt` is reserved: PowerDocs stores the words ignored by the user in it.

3. Declare the language in the `languages` array of the `index.json` file at the root of the repository:

```json
{
  "lcid": 1036,
  "culture": "fr-FR",
  "name": "French (France)",
  "description": "Short description of the language data.",
  "version": "1.0.0",
  "sourceUrl": "https://example.com/dictionaries",
  "license": "MPL-2.0",
  "files": []
}
```

| Field | Required | Description |
|---|---|---|
| `lcid` | yes | Windows language identifier, as a decimal integer. It is also the name of the language folder. |
| `culture` | yes | Culture tag (BCP-47) matching the LCID, such as `fr-FR`. |
| `name` | yes | English name of the language. PowerDocs displays the native name of the culture when it knows it. |
| `description` | no | Short description. |
| `version` | yes | Published version (`major.minor[.build]`). |
| `sourceUrl` | no | Origin of the dictionary and its terms of use. |
| `license` | no | License of the data, preferably as an SPDX identifier. |
| `files` | yes | Files downloaded by PowerDocs: `name`, `type` (`affix`, `dictionary`, `thesaurus`, `rules` or `notes`), `size` in bytes and `sha256`. |

4. Fill the `files` array automatically:

```bash
python3 .github/scripts/validate_languages.py --update-index
```

PowerDocs checks the size and the SHA-256 digest of each file after download: never edit them by hand.

## Style rules

`language-rules.json` describes the data consumed by the generic analysis rules of the editor:

```json
{
  "locale": "fr-FR",
  "version": 1,
  "fillerWords": [
    { "text": "du coup", "explanation": "Optional explanation shown to the user." }
  ],
  "anglicisms": [
    { "word": "feedback", "suggestions": ["retour", "avis"], "explanation": "Optional." }
  ],
  "homophones": [
    { "id": "ou-vs-ou-accent", "forms": ["ou", "où"], "explanation": "Optional." }
  ],
  "spaceBeforePunctuation": {
    "required": ["?", "!", ":", ";"],
    "forbidden": []
  },
  "autoCorrections": [
    { "from": "ca va", "to": "ça va" }
  ]
}
```

`locale` must be the culture tag of the language.

## Publishing an update

Increase `version` in `index.json` whenever a file of the language changes, then run the script with
`--update-index`. PowerDocs offers the update when the published version is newer than the installed one.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the rules checked on every pull request.
