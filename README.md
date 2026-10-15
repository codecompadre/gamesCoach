# gamesCoach

A personal command-line coaching console that brings OpenAI and Google Gemini into the same discussion as dedicated CrossFit athlete-coaches. Its scope is CrossFit workouts, skill development, training, recovery, performance fueling, and competition preparation.

The coaches decline unrelated requests. Strength, aerobic work, nutrition, and recovery guidance must serve your CrossFit performance and readiness.

Both models receive your athlete profile, request, and the shared discussion. They propose, critique, and refine the plan; OpenAI then synthesizes the final response. Plans and the discussion are saved locally so you can review or export them later. Chat mode supports follow-up conversations and can use your saved menu entries.

## Get started

Requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/codecompadre/gamesCoach.git
cd gamesCoach
uv sync --locked
uv run gamescoach
```

### Ubuntu on Windows (WSL)

Open your **Ubuntu terminal** on Windows. If `uv` is not installed, use its official installer and load its shell environment:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

Then clone and launch the offline interactive console:

```bash
git clone https://github.com/codecompadre/gamesCoach.git
cd gamesCoach
uv sync --locked
uv run gamescoach --demo
```

If you already cloned the repository, run `git pull --ff-only` from that checkout instead of cloning again. `uv` can download a compatible Python interpreter if needed. If Git or curl is missing, install it through Ubuntu's package manager. Demo mode works without API keys; for live coaching, follow **Connect both models** below, then run `uv run gamescoach` without `--demo`.

The interactive console guides you through creating a profile and requesting a plan. You can try a static sample without API credentials:

```bash
uv run gamescoach plan --demo --kind workout \
  --request "A 45-minute CrossFit session" --show-debate
```

**Demo mode is an offline sample, not a tailored workout.** It makes no API calls and does not adapt its sample content to your profile or request.

## Connect both models

Live plans require an OpenAI API key and a Gemini API key. ChatGPT and Gemini web subscriptions are separate from API access and billing. Obtain API access through your provider accounts and keep your keys private.

Set the keys in your securely configured environment. For an interactive Bash session, these prompts avoid placing the values directly in shell history:

```bash
read -rsp "OpenAI API key: " OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
read -rsp "Gemini API key: " GEMINI_API_KEY
printf '\n'
export GEMINI_API_KEY

uv run gamescoach doctor
```

Accepted key names:

| Provider | Environment variables |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` or `GAMESCOACH_OPENAI_API_KEY` |
| Gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or `GAMESCOACH_GEMINI_API_KEY` |

`.env.example` documents the settings; **the app does not automatically load `.env` files**. Export variables in your shell or configure them securely in your execution environment. Never paste keys into chat, a plan request, your athlete profile, or a committed file.

Model defaults are `gpt-5-mini` and `gemini-flash-latest`. Google's Flash alias follows its current Flash model, so the underlying version and pricing can change. Set an explicit supported model ID if you need to pin a version. Override models with environment variables:

```bash
export OPENAI_MODEL="gpt-5-mini"
export GEMINI_MODEL="gemini-flash-latest"
```

## Use the console

Create or update your local athlete profile, then request a plan:

```bash
uv run gamescoach profile
uv run gamescoach profile show
uv run gamescoach plan --kind workout \
  --request "A 45-minute session with a barbell and rower; prioritize pacing" \
  --show-debate
```

Choose a plan kind that matches your goal:

| Kind | Purpose |
| --- | --- |
| `workout` | A session with warm-up, strength or skill work, conditioning, and scaling |
| `training` | CrossFit progression, skill development, conditioning, and recovery |
| `nutrition` | Food, hydration, and fueling for CrossFit training and competition |
| `competition` | CrossFit preparation, pacing, standards practice, and taper considerations |

Include relevant limits in your profile or request: experience, equipment, available time, recent training, injuries, recovery, dietary needs, and competition date or published rules. The models should identify missing information rather than assume it.

The default discussion uses two rounds and five API calls: two calls to each provider followed by an OpenAI synthesis. `--rounds 1` uses three calls; `--rounds 3` uses seven. Three rounds is the maximum. There are no automatic provider retries by default. More rounds use more tokens and may cost more; provider rates and model availability depend on your account.

```bash
uv run gamescoach plan --kind training --rounds 2 \
  --request "Plan four CrossFit training days per week for the next month"
uv run gamescoach history
uv run gamescoach show ID --transcript
uv run gamescoach export ID --output plan.md
```

Replace `ID` with a saved plan ID from `history`. `--show-debate` shows the discussion when creating a plan; `show ID --transcript` retrieves it later.

## Talk to the coach

Choose **8 — Chat** from the main menu. If you have saved conversations, press **Enter** to continue the most recently saved chat, choose **2** to start a new one, or **3** to pick an older conversation from a numbered list. Use **b** to return to the main menu. With no saved chats, a new conversation starts immediately. Live and demo chats appear separately.

You can also start directly:

```bash
uv run gamescoach chat
# Offline sample conversation, without API calls:
uv run gamescoach chat --demo
```

Type a message at the `You>` prompt, then follow up naturally. Both coaches receive the conversation so far; the console shows their combined athlete-facing answer. They remain CrossFit athlete-coaches, including when answering questions about your plans, aerobic capacity, skills, recovery, fueling, or competition preparation.

New chats load your saved athlete profile and the five most recent entries from the other menu options and chat history. The console identifies those entries by ID. It uses saved plans' original requests and final recommendations; referenced chats contribute their full athlete/coach conversation. Saved recommendations are context, not proof that you performed those workouts or followed that nutrition plan. To discuss an older entry, use `/read ID`.

| Command | Action |
| --- | --- |
| `/quit` or `/ quit` | Return to the main menu; exit if launched with `gamescoach chat` |
| `/back` or `/exit` | Same as `/quit` |
| `/help` | Show the chat commands |
| `/history` | List your saved plans and conversations |
| `/profile` | Show your saved athlete profile |
| `/read ID` | Add a specific saved entry to the coaching context |
| `/file [PATH]` | Import a training file; prompts for its path when omitted |
| `/learn [PATH]` | Add a methodology PDF to the persistent coaching library |
| `/library` | List your saved methodology guides |
| `/sources` | Inspect the automatically loaded methodology brief and passages selected for the latest question |
| `/clearrefs` | Remove loaded entry references while retaining the current conversation |
| `/new` | Start a fresh conversation while retaining the profile and selected references |

Completed exchanges are saved locally under one conversation ID. Returning to the menu does not delete them. Review, export, or resume a conversation:

```bash
uv run gamescoach history
uv run gamescoach show ID
uv run gamescoach export ID --output conversation.md
uv run gamescoach chat --resume ID
# Resume an offline conversation in offline mode:
uv run gamescoach chat --demo --resume ID
```

Chat `show` and export include the full athlete/coach conversation. Add `--transcript` to `show`, or `--show-debate` to `chat`, to inspect the models' discussion. Live and demo conversations cannot be mixed when resuming. A resumed conversation restores its saved reference IDs where the entries are still available, without adding recent entries automatically. `/read` and `/clearrefs` changes to an existing conversation are saved immediately; no extra model call is needed.

Each live message uses one discussion round by default: OpenAI drafts, Gemini reviews, and OpenAI answers, for **three API calls per message**. `chat --rounds 2` uses five calls per message and `--rounds 3` uses seven. Follow-ups include earlier messages, so longer conversations use more tokens. Slash commands do not call either API. The app stops before an oversized request and suggests starting a new chat or reducing references rather than silently forgetting earlier conversation. Use `chat --context-limit 0` to begin a new chat without automatic references, or `/clearrefs` to clear them while chatting. Clearing references keeps your conversation; information already discussed remains in that dialogue. If automatic references are too large at menu startup, the console explains that it is starting without them so you can choose entries with `/read`.

Demo chat replies are static examples. They exercise conversation storage and navigation but do not interpret your message or tailor coaching to your information.

## Read your weekly training document

Choose **9 — Import training file** in the main menu, or import directly while chatting:

Type `/file` on its own to be prompted for the file path, or include the path in the same line:

```text
/file "C:\Users\YourName\Documents\Week 1.docx"
```

For Ubuntu on Windows, right-click the document in Windows File Explorer, choose **Copy as path**, then paste that path after `/file`. Windows drive paths are translated to `/mnt/c/`, `/mnt/d/`, etc. Quotes and spaces in filenames are supported. Linux paths work too:

```text
/file "/mnt/c/Users/YourName/Documents/Week 1.docx"
```

Then ask a question, for example: “Review this week's workouts for aerobic capacity and help me pace Monday's intervals.” Both coaches receive the extracted training text alongside your chat and athlete profile.

You can also import from the terminal:

```bash
uv run gamescoach import-file "/mnt/c/Users/YourName/Documents/Week 1.docx"
uv run gamescoach
```

Supported formats are **`.docx`, `.txt`, and `.md`**. Word's main document paragraphs and workout tables are read in order, including nested tables. Embedded images, scans, headers, footers, comments and tracked-change text are not extracted. Save older `.doc` files as `.docx` in Word. Password-protected documents and PDFs are not supported. Files are limited to 10 MB and extracted text to 60000 characters; split larger programs into individual weeks.

Importing makes **no API requests** and does not modify the original file. Only the extracted text is saved locally and sent to both providers when you ask a live coaching question; the original Word file and full directory path are not uploaded. Include only the training material you want to share, without API keys or other secrets. Word files and the optional `.training-files/` folder are ignored by Git; keep personal text/Markdown training files outside the checkout or in that ignored folder.

Imported documents appear in history, and new chats include recent entries automatically. `/file` adds the document to the current chat and saves that reference immediately if the conversation already exists. Resume the conversation to keep using it without locating the file again, or use `/read ID` to load any previously imported document. Edits to the original file require importing it again; existing imported copies are preserved.

## Give the coaches methodology PDFs

Version **0.4.0** prepares retained PDF knowledge automatically at startup and can check a folder for new or changed guides. Register your folder once from the Ubuntu terminal:

```bash
uv run gamescoach library watch 'C:\Users\YourName\Desktop\CrossFit'
uv run gamescoach
```

The equivalent WSL folder path is `/mnt/c/Users/YourName/Desktop/CrossFit`. Windows **Copy as path** works too. The folder must be accessible from the computer running the CLI. You can also choose **10 — Methodology PDF library** in the menu or type `/learn` in chat and paste the folder path. The app imports PDFs directly inside that folder, including `.PDF` files, and checks for new or changed files each time you start the menu, chat, or a plan. It does not scan subfolders. Unchanged files reuse their saved text and prepared index.

To import individual PDFs instead, use `/learn` with each document's path:

```text
/learn "C:\Users\YourName\Downloads\CrossFit Methodology.pdf"
/learn "C:\Users\YourName\Downloads\Competitor Methodology.pdf"
/library
```

Or add them from your Ubuntu terminal:

```bash
uv run gamescoach library add "/mnt/c/Users/YourName/Downloads/CrossFit Methodology.pdf"
uv run gamescoach library add "/mnt/c/Users/YourName/Downloads/Competitor Methodology.pdf"
uv run gamescoach library list
```

Adding a guide extracts and saves its text locally, without sending it to an AI or making paid API requests. The app prepares a searchable index and a compact foundation brief made from cited source passages. On startup, it loads this retained knowledge automatically and shows **CrossFit knowledge ready** with the guide and page counts. Existing imported guides are prepared automatically after updating; you do not need to import them again or tell the coaches to read them.

Both coaches receive the foundation brief automatically on **every live chat question and all four plan types**, alongside passages selected for the question. Ask normally, for example: “How should I improve my aerobic capacity while keeping enough competition skill practice?” The guides remain active across app restarts, new chats, resumed chats, `/new`, and `/clearrefs`, separate from the five recent menu entries. Type `/sources` even before your first live chat message to see the loaded brief; after a reply, it also shows the selected passages. Demo replies remain static and do not apply the guides.

The compiled index and source-grounded brief are saved locally and reused across restarts. Adding or removing a guide refreshes the prepared knowledge; adding a PDF or folder during chat makes it available immediately. Startup makes no AI requests. It checks a configured source folder for changes and reads new or changed PDFs; unchanged PDFs use their retained text. Caching avoids repeating local text preparation on every question; the providers still take time to generate their answers.

The brief contains up to eight short foundation passages, and question-specific retrieval supplies up to eight passages totaling at most 16000 serialized characters. The app uses keyword relevance to select source material, so each model receives bounded context, not every page of every book. Both models are instructed to apply relevant principles automatically, cite `[filename, PDF p. N]`, distinguish source guidance from athlete-specific adjustments, and respect recovery, limitations and CrossFit scope. PDF page numbers refer to physical PDF pages, which may differ from printed page numbers.

The app retains the knowledge; neither model permanently learns the books or performs a full-book reading at startup. The brief is extracted source text, not an AI-written summary of every page. Selection can miss relevant sections, so a specific topic or PDF page can help. The brief and passages used for a completed reply are saved with the plan/chat for review. Older competition rules in a guide are not verified current rules.

The library supports up to six text-based PDFs, each up to 25 MB, 600 pages and two million extracted characters. Scanned/image-only guides need OCR first; images and diagrams are not interpreted, and pages without extractable text are reported. Importing the same exact PDF twice does not duplicate it. Individually reimported edited PDFs become separate entries; successfully imported revisions in the watched folder replace their previous library entries. Updating an edition needs a free library slot; if all six are occupied, remove an unused entry first. Deleting a source file keeps its imported knowledge until you remove the library entry.

Use `uv run gamescoach library unwatch` to stop checking the folder while keeping imported knowledge. If the folder is temporarily unavailable, startup warns and continues using previously imported guides. Remove a guide with `uv run gamescoach library remove ID`, using the ID from `library list`. An unchanged watched file stays removed until it changes or you register the folder again. Original PDFs, older conversations and previously saved source excerpts remain intact. The library and its prepared cache are unencrypted private local data under the app's existing data directory. The source folder path stays local. Only the brief, selected text passages and filenames go to both providers during live coaching; original PDFs and their directory paths are not uploaded. Keep API keys out of source documents. PDF files are ignored by Git.

## Customize the coaching emphasis

The built-in coaching instructions keep both models focused on CrossFit. Optional coach notes let you customize their emphasis and tone without editing those instructions:

```bash
cp coach-notes.example.md coach-notes.local.md
# Edit coach-notes.local.md with your preferred coaching approach.
export GAMESCOACH_COACH_NOTES_FILE="$PWD/coach-notes.local.md"
uv run gamescoach doctor
```

`coach-notes.local.md` is ignored by Git. The example contains coaching preferences, not a personal athlete profile; keep your actual training limits in your saved profile or request. Notes are read when configured through `GAMESCOACH_COACH_NOTES_FILE` and are shared with both providers during live planning. Do not put secrets in them. All notes remain subordinate to the fixed CrossFit scope and system constraints.

## Your data

Profiles, plans, conversations, and model discussions are stored as unencrypted local JSON under `GAMESCOACH_DATA_DIR` when set, otherwise `$XDG_DATA_HOME/gamescoach` or `~/.local/share/gamescoach`. The app creates its data directory and files with private permissions. Exports contain plan text or the full athlete/coach conversation; choose a private destination if they contain personal information.

Keep custom data directories outside the checkout, or use the ignored `.gamescoach/` or `gamescoach-data/` directories. PDF and Word files are ignored regardless of filename capitalization; personal text/Markdown files and exports belong outside the checkout or in `.training-files/`. Git ignore rules do not protect files already committed or added with `git add --force`. Collaborators should configure their own API keys; cloning the repository does not copy your local environment variables or saved training history.

In live mode, your profile, request, any configured coach notes, and shared model critiques are sent to **both OpenAI and Google**. Chat also sends prior messages and the saved entries loaded as references. Local storage does not make those requests offline. Provider handling of API data follows the terms and settings of your accounts. Demo mode sends nothing to either provider. API keys are read from the environment for authentication and are not stored in the conversation files.

## Training responsibly

Use the output as a planning aid. Explain injuries and current training tolerance before asking for progression; seek qualified help for pain, injury management, or medical nutrition needs. Review suggested loading, scaling, recovery, deloads, and tapers against your actual readiness. Nutrition guidance should support meals, hydration, and fueling, without crash diets or invented calorie targets when the necessary data is missing.

AI-generated plans do not guarantee competition qualification or performance. CrossFit Open and Quarterfinals rules can change. The coaches should flag rules that need verification instead of inventing eligibility or year-specific thresholds. The app does not browse for current rules: check the official rules, movement standards, division requirements, and schedule yourself, and include them in the request when relevant.

## Check and troubleshoot

If `/file` is unknown or the menu does not show **9 — Import training file**, exit the app completely and update from your `gamesCoach` checkout:

```bash
git pull --ff-only
uv sync --locked --reinstall-package gamescoach
uv run gamescoach --version
uv run gamescoach
```

Version `0.2.0` includes the file-path prompt and version display. An already running app keeps its previously loaded code until restarted. Use `uv run gamescoach` from this checkout to launch its local installation, rather than an older globally installed command. Inside chat, `/help` lists the supported slash commands, including `/file [PATH]`.

If PDF import warns that `fontTools is required` for a CFF Type1 font, the import completed with a fallback font mapping, which may affect extracted characters. Version `0.4.1` includes pypdf's font support. Update with the commands above and restart the app: PDFs already imported from your watched folder are re-extracted once with the improved parser, then cached normally. For a guide added individually, use `library add PATH` or `/learn PATH` again with its original PDF. Reprocessing keeps the same guide ID and does not duplicate it or call either AI provider.

```bash
uv run gamescoach doctor
uv run gamescoach doctor --check-api
uv run pytest
```

`doctor` alone checks local configuration without API requests. `doctor --check-api` sends one short generation request to each configured provider, returns failure if either fails, and may incur API charges. It sends no athlete profile or saved conversation and never displays keys. This tests actual generation: a model appearing in Google's listing does not guarantee its generation endpoint works.

If Gemini reports that `gemini-2.5-flash` is unavailable, set `export GEMINI_MODEL="gemini-flash-latest"` in the same terminal and restart the app. An existing `GEMINI_MODEL` environment setting overrides the new default even after pulling updates. Check both keys are exported in the same shell, API billing and quotas are available, and the selected models are enabled for your accounts. For connection errors, verify access to `api.openai.com` and `generativelanguage.googleapis.com`. Keep TLS verification enabled.

The offline tests exercise the CLI and provider integration with simulated responses. Successful tests do not establish live API access; confirm that with a live plan after configuring both accounts.
