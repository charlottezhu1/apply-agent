# Job App Autofill (Chrome extension)

A Manifest V3 extension that fills job application forms (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and most custom forms) with your profile (`applicant/profile.yaml`). Everything runs locally and no data leaves the browser. **It never submits a form.**

## Install

1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and select this `chrome_extension/` folder.
3. Optional: pin the extension from the puzzle-piece menu.

After editing any file here, click the **reload ↻** icon on the extension card, then refresh the application page.

## Use

On an application page, use any of these:
- the floating blue **Autofill** button (bottom right; it appears when the page looks like a job form)
- the toolbar popup → **Autofill this page**
- the keyboard shortcut **Alt+Shift+F** (change it at `chrome://extensions/shortcuts`)

Then **review every field**, attach the resume PDF, and submit it yourself. To see exactly what was matched, open DevTools → Console and look for the `[autofill]` log, which shows each field as filled, skipped or FAILED.

## Configure

**Your values** (name, email, school, work authorization, EEO answers, ...) are in `applicant/profile.yaml`, not in this folder. Run `python -m apply_agent.sync` (every pipeline run does it too) and it writes `generated/profile.json`, which `content.js` reads fresh on every fill, so there is nothing to reload. Name fields have everyday versions (`firstName`, `fullName`) and legal versions (`legalFirstName`, `fullLegalName`): a field labeled "legal", "passport" or "government" gets the legal name, and every other name field gets the everyday one. Dropdown answers (`workAuthorized`, `needsSponsorship`, `veteran`, `disability`, `gender`, `race`) are matched against the option text. If `profile.json` is missing, the floating button says "No profile: run sync" and nothing is filled.

**The matching rules** are in `config.js`. They are generic, so you rarely need to touch them:

| Setting | What it is |
|---|---|
| `RULES` | An ordered list of `{key, re}`. The regex is tested against each field's `autocomplete`, `name`, `id`, placeholder, `aria-label`, `data-automation-id` and label text. **The first match wins**, so specific rules go above general ones. `key: "SKIP"` makes sure a field is never filled (references, usernames, other people's names, social links). |
| `LEGAL_RE` | Which label text switches a name field to the legal name |
| `SHOW_FLOATING_BUTTON` | Set to `false` to use only the popup and the shortcut |

To support a new field, add a key to `form_profile()` in `apply_agent/core/profile.py` (and the matching field in `profile.yaml`), then a `RULES` entry above any more general rule that would otherwise catch it. A test fails if a `RULES` key has no source in the profile.

Questions that contain another field's keyword ("require **employer** sponsorship", "undergraduate **degree**") are handled by a block of rules near the top of `RULES`, so they win over the general rules. `undergradSchool` and `testScores` (SAT) come from the profile's `education` section.

## How it works (`content.js`)

- **Finding fields:** it collects every visible, enabled input, textarea and select, **including inside open shadow roots** (SmartRecruiters) and in every frame (`all_frames: true`, for embedded Greenhouse forms).
- **Labels:** it uses `<label for>`, then `aria-labelledby`, then walks up the DOM to the nearest `label`/`legend` that belongs to only this field (for Ashby, where labels aren't linked).
- **Filling:**
  - Text fields use the native value setter plus input/change events and a real focus and blur, so React-controlled forms (Ashby) register the value.
  - Native `<select>` elements are matched exactly, then by "contains".
  - Comboboxes and typeaheads (Greenhouse react-select, Lever, Google Places) get typed into, then it waits for the options to appear and clicks the best one. Location fields accept the first suggestion.
  - Date fields read the placeholder (`MM/DD/YYYY`, `YYYY-MM-DD`, `MM/YYYY`, …) and fall back to typing one key at a time for masked inputs.
- **Safety:** fields that already have a value are never overwritten. File inputs, checkboxes and radio buttons are left alone, and so is any submit button.

## Files

| File | Role |
|---|---|
| `manifest.json` | MV3 manifest: content scripts on all URLs, popup, Alt+Shift+F command |
| `config.js` | The matching rules (generic; your values are in `applicant/profile.yaml`) |
| `content.js` | Field detection and filling, plus the floating button |
| `popup.html` / `popup.js` | Toolbar popup that runs the fill in all frames and shows the count |
| `background.js` | Keyboard shortcut handler, which shows the fill count as a badge |

## Per-job answers from the pipeline

The [submission preparer](../apply_agent/prepare/README.md#4-autofill-packets-for-the-chrome-extension) writes `generated/packets.json` with each tailored job's drafted essay answers. On every fill, `content.js`:

1. Reads `generated/packets.json` fresh (it's listed in `web_accessible_resources`), so new answers work without reloading the extension.
2. Picks the packet whose `match` key appears in the page URL, the referrer or the top frame's URL (for embedded forms). If zero or several packets match, it fills no answers.
3. For each empty `textarea` or text input that no `RULES` entry claimed, compares the label with the packet's questions (word overlap of at least 0.6) and fills the best match.
4. Logs those fills as `filled (answer): <label>` in the `[autofill]` console log.

After pulling this change, reload the extension once at `chrome://extensions`, since the manifest changed.

Resume upload stays manual for now. The packet's `resume_pdf` path is shown in SUMMARY.md so you can pick the right file.

## Optional later: attach the right resume automatically

This takes about 1–2 hours to build and touches about 60 lines. It's optional, since manual upload works.

1. **Stage 4 copies each day's PDFs** into `chrome_extension/resumes/{Company}_{ROLE}.pdf` and puts that path in the packet as `resume_file`.
2. **In `manifest.json`, make them readable by the content script:** `"web_accessible_resources": [{"resources": ["resumes/*"], "matches": ["<all_urls>"]}]`. A content script can't read arbitrary files on disk, only files inside the extension folder.
3. **In `content.js`, fetch the file and drop it into the upload field.**
   - Fetch the PDF from `chrome.runtime.getURL(packet.resume_file)`.
   - Wrap it as `new File([blob], "Firstname_Lastname_Resume.pdf", {type: "application/pdf"})`. The recruiter sees a clean filename, not `Stripe_SWE_…`.
   - Put the file into the resume `<input type=file>` using a `DataTransfer` and set `input.files`.
   - Fire `input` and `change` events.
4. **Pick the resume input, not the cover-letter input.** Use the same label matching as the other fields: `/resume|cv/` attaches, and `/cover/` is skipped. If no unique packet matches the page, **attach nothing**. Show `Attached: Stripe_SWE` on the floating button so a wrong match is visible right away.

| Site | Expected |
|---|---|
| Greenhouse, Lever | Works. They use a standard file input |
| Ashby | Usually works. If the dropzone ignores `change`, also fire `drop` with the same `DataTransfer` |
| SmartRecruiters | Probably works. The input is inside shadow DOM, which `collect()` already walks |
| Workday | Unreliable. Leave it manual |

If an upload isn't picked up, the upload area still shows empty, and you attach the file by hand as you do today.

## Test checklist

Each of these should fill name, email, phone, LinkedIn, GitHub, school, location, sponsorship and authorization, and nothing else:
- [ ] Greenhouse (embedded form and `job-boards.greenhouse.io`)
- [ ] Lever (`jobs.lever.co/.../apply`)
- [ ] Ashby, where the values survive clicking Submit validation with no "Missing entry" errors
- [ ] SmartRecruiters (shadow DOM)
- [ ] Workday (after signing in; one page at a time)
- [ ] Reference and emergency-contact name fields stay empty
- [ ] Fields you've already typed into are left unchanged
