## Style guide for English technical documentation (YDB)

Follow these rules when TARGET language is English.

### Tone and audience

- Direct, professional, conversational where SOURCE is conversational. Address the reader as **you**.
- Prefer sentences ≤20 words when SOURCE is short; one main idea per paragraph.
- **Global English**: American spelling; avoid US-only idioms. Many readers are non-native — avoid ambiguity.
- Lead with the key point; do not bury the main action.

### Voice

- **Active voice** preferred: «Click **Save**» not «The file is saved by clicking **Save**».
- **Present simple** for procedures: «When you click the icon, a new window appears.»

### Contractions

- Use with pronouns/negatives: you're, don't, can't.
- Do not contract product names: not «YDB's» unless SOURCE uses a possessive for a generic noun.

### Lists (critical for RU → EN)

Russian lists often end items with **;** — **never copy semicolons into English lists.**

- Start each item with a capital letter.
- No semicolons at end of items.
- Short items (1–3 words): period optional. Full sentences: period required.

Example:

- SOURCE: «- установлены все зависимости;»
- GOOD: «- All dependencies are installed.»
- BAD: «- all dependencies are installed;»

### Capitalization

- **Sentence case** in headings: «## Configure a connection to the service» not Title Case Every Word.

### Punctuation

- **Oxford comma**: A, B, and C.
- Straight double quotes `"` for UI phrases when not using bold/backticks.
- Punctuation usually **outside** closing quotes unless part of the quoted UI string.
- **Do not use semicolons** to join independent clauses — use two sentences.
- In running text prefer **or** over `/` (slashes OK in UI labels).

### Hyphens and dashes

- Hyphen in compound adjectives before a noun: read-only field, two-factor authentication, upper-right corner.
- No hyphen after adverbs ending in -ly: automatically processed (not automatically-processed).
- En dash for ranges without spaces: 12:00–15:00, $56–$560.

### Numbers and units

- Spell out one through nine in prose; numerals in UI examples.
- Space before units: 16 GB, 20 °C. No space before %: 14%.
- Metric units and 24-hour time: 16:00, UTC+3.

### YDB-specific

- Keep brand names verbatim: YDB, YQL, Yandex Cloud, Diplodoc, {{ ydb-short-name }}.
- CLI flags and paths unchanged; translate comments in code only when the fragment type allows it.
- Do not replace backticks with quotation marks.
