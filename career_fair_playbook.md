# Career Fair in a Breeze: a playbook for working through it with Claude

**The problem:** 150 booths, four hours, and no way to know who is worth your time, what they actually do, or what to say. Most people wander, collect swag, and forget every name by dinner.

**The idea:** do the thinking *before* you walk in, so the fair itself is just execution.

> Cut the list → know each company in one line → one pitch hook each → walk in booth order → write notes somewhere that can't disappear → apply in one batch that night.

---

## Read first: two things to bring and expect
1. **Bring paper resumes.** Many employers collect applications online, but a printed resume still helps a recruiter put a face to your name and remember you. Bring a handful.
2. **Expect bad Wi-Fi.** Fair venues are crowded and connections drop. If the internet is flaky, **write your notes on a paper notepad or in your phone's offline Notes app. Don't trust a website to save them.** Copy them into your tracker later.

---

## Step 0: Upload your resume and answer a few questions (2 minutes)
Everything downstream depends on this. **Upload your resume** (PDF or file) to Claude instead of retyping it, and let Claude pull your proof points from it. Then answer its questions:
- Target roles (e.g. SWE, PM) and graduation date
- **Work authorization**: do you need OPT/CPT now? sponsorship later?
- Location preference (e.g. Bay Area or remote)
- What you *don't* want (e.g. defense, hardware, consulting)

*Prompt:* "Here is my resume [upload]. Ask me what you need to know to filter a career fair for me: roles, graduation date, work authorization, location and dislikes. Then summarize my 5 strongest proof points with numbers."

## Step 1: Paste the fair link and let Claude pull everything
Don't read the list by hand, and don't click through it yourself. Whether the fair is on Handshake or any other site, **paste the fair link into Claude and follow its instructions.** Claude drives a browser and does the work: it opens the employer list, loads every employer, expands each one, and reads the hidden details.

Your only job:
- **Sign in once** when the site asks. Sites like Handshake need your school login. You do it yourself; Claude never needs your password.
- If Claude can't use a browser, select-all on the page and paste the text into chat.

What Claude should collect for each employer:
- Company, booth, and what they do
- Job titles, job type and employment type
- **Work authorization** ("willing to sponsor" / "accepting OPT/CPT" / "required" / "not required")

On Handshake, these details are hidden behind each employer's "More" button, and the list loads in pieces as you scroll. Claude should open all of them and check that the count matches the fair page (for example, 144 employers).

*Prompt:* "Here is my career fair link: [link]. Open it, pull every employer with company, booth, what they do, roles, and work-authorization field, and put it in a table. Tell me if I need to sign in."

## Step 2: Filter in layers, not by gut feel
Do cuts in this order:

1. **Hard filters first** (they save the most time): work authorization, role types, location.
   Note: a company's Handshake setting is self-declared. If you saw a job description that says "no sponsorship," trust that over the profile.
2. **Fit second:** which of the survivors match your background and what you want?
3. **Star last:** pick the ~10 that deserve your best energy. Everything else is a "stop if the line is short."

*Prompt:* "Filter to employers that [sponsor / accept OPT]. Then star the ones that best match my background: [proof points]. Explain each star in one line."

Result: however many employers the fair has → 20-50 left after filtering → ~10 starred.

## Step 3: Know each company in one line
Add two columns: **what they do** (1-2 sentences) and **approximate size/stage** (early startup, growth, public, very large). It changes what you say. A 20-person startup wants ownership and speed; a large company wants a clear specialty and a team to join.

## Step 4: One tailored pitch per starred company
Format that works: **Lead with → proof.**
- **Intro:** Name, year, looking for which role, and so on (customize for each company).
- **Lead with:** pitch the one thing from your background that matches *their* problem.
- **Proof:** one number or shipped result.
- **Ask questions** after they show interest, such as which team hires new grads and (if relevant) "Do you sponsor H-1B after OPT?" Skip questions their website answers.

Template: "Hi, I'm [Name], a [year] looking for a [role]. I built a [project] that reached [X%] [metric] with [method/tool], and I saw your company focuses on [their area]. Do you have teams working on [their area] hiring new grads?"

Filled in: "I built an LLM-judge that reached 96% agreement with human raters using human-labeled eval data, and I saw your company focuses on AI evals. Do you have teams working on evals hiring new grads?"

## Step 5: Turn the list into a route
- Sort by **booth number** and group adjacent booths into zones. (Assume nearby numbers are nearby. Check the floor map once.)
- **Round 1 = stars only**, first, when lines are shortest. **Round 2 = the rest**, only if time and energy remain.
- Hit the biggest-name booths early, before lunch lines.
- Warm up at a lower-stakes booth before your top choice.

## Step 6: Take notes somewhere that can't vanish
This is where it went wrong for me, so learn from it.

Per booth, capture: **name, what you discussed, next step, and the link to submit your resume.**

What to use:
- **Safest and simplest:** your phone's Notes app or a shared spreadsheet with one row per company. Pre-fill the rows from the plan.
- **Best: a notes site Claude builds for you in minutes** (see "Build a notes site" below).
- **Never rely on a web page's browser storage.** It can silently disappear. I lost hours of notes this way.
- Make a backup every hour: copy your notes and paste them into a message to yourself.

### Build a notes site with a backend, fast
Instead of a spreadsheet, ask Claude for a small page you open on your phone, with one card per company in booth order. It takes about 10 minutes.

*Prompt:* "Build a phone-friendly notes page from my plan: one card per company in booth order, with the booth number, a star, what they do, my pitch, a visited checkbox, a field for the link to submit my resume, and a notes box. **Save every edit to a server-side database** (not browser storage), show a visible 'Saved' status, and let me read it back to you later."

Requirements that matter:
- **Server-side saving.** Edits must survive a closed tab, a dead battery and a day of classes.
- **A visible "Saved" indicator**, so you can see it work.
- **Test before the fair.** Type a note, close the page, reopen it, and check it's still there.
- **Open it on the phone you'll carry**, in the same signed-in browser each time, and add it to your home screen.
- **Claude can read the saved notes back**, so you can say "summarize my notes and list the links" that night, with no copy and paste.
- Keep it simple: a checkbox, a link field and a notes box. Don't add features before the fair.

### Voice notes (optional)
- **Use your phone's keyboard microphone** to dictate into the notes box. On iPhone it works in any text field with no setup. Dictate after you step away from the booth, not during the conversation, and don't record people without asking.
- A built-in "listen and summarize" button in the page is possible, but the page's dictation feature doesn't work in Safari or the Claude mobile apps, so on an iPhone it won't appear. Check your browser before spending time on it.
- A fair is loud, so expect garbled names. Fix names and links right after dictating.
- Summarize later: paste the dictated notes to Claude that night and ask for contact, what was discussed and next step per company.

## Step 7: Batch the applications that night
Collect all the "submit resume" links. Apply within 24-48 hours, mention the conversation, and email each contact a short note referencing something you discussed. Tailor the resume per role (Claude can help).

---

## Quick checklist
- [ ] Profile written (roles, grad date, work authorization, location, proof points)
- [ ] Employer list pulled with the hidden detail fields expanded
- [ ] Filtered by hard constraints, then fit, then ~10 stars
- [ ] One-line "what they do" and size for each
- [ ] Pitch (lead with → proof → ask) for each star
- [ ] Booth-ordered route, stars first
- [ ] Notes site built (server-side save, "Saved" status), tested, and open on your phone
- [ ] Hourly backup plan (copy notes to yourself)
- [ ] Evening: apply in one batch and send follow-ups

## Caveats
- Handshake employer fields are what the employer entered, not verified policy.
- "Willing to sponsor" doesn't guarantee a specific role is eligible. Confirm at the booth.
- Company sizes and descriptions may be approximate or out of date.
- Don't put personal details (phone, email, GPA) in anything you share.
