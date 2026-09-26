# powerdocs-opendata

The store for the language data used by PowerDocs: spelling dictionaries, thesauri and style rules.

PowerDocs lists the languages declared in [`index.json`](index.json) in its **Language data** window,
and downloads them straight from the `main` branch.

## Repository layout

```
index.json                  Languages offered in PowerDocs
lcid.md                     Reference table of the Windows language identifiers (LCID)
dictionaries/
  1036/                     One folder per language, named after its LCID
    fr_FR.aff               Hunspell affix file (required)
    fr_FR.dic               Hunspell dictionary (required)
    fr_FR.dat               MyThes thesaurus (optional)
    language-rules.json     Style rules: filler words, anglicisms, homophones... (optional)
```

See [dictionaries/readme.md](dictionaries/readme.md) for the format of each file and of `index.json`,
and [CONTRIBUTING.md](CONTRIBUTING.md) for the rules checked on every pull request.

## Available languages

| LCID | Culture | Language |
|---|---|---|
| 1031 | `de-DE` | German (Germany) |
| 1033 | `en-US` | English (United States) |
| 1036 | `fr-FR` | French (France) |
| 1040 | `it-IT` | Italian (Italy) |
| 3082 | `es-ES` | Spanish (Spain) |
