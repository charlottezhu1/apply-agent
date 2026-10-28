// Matching rules for the autofill extension. Nothing in this file is about a person.
// YOUR values (name, email, school, ...) live in applicant/profile.yaml. `python -m apply_agent.sync` (and every
// pipeline run) copies them to generated/profile.json, and content.js loads that file into PROFILE on each fill.
// After editing this file, go to chrome://extensions and click the reload arrow on this extension.

let PROFILE = {};

// Each rule: which profile field to use, and a regex tested against the field's
// name, id, placeholder, label, aria-label and autocomplete attributes.
// Order matters. The first matching rule wins, so keep specific rules above general ones.
// Questions you would answer "No" to at nearly every company. Matched against the question text.
// Questions about sponsorship, authorization, willingness or experience are excluded so they reach their own rules.
const NO_QUESTIONS = new RegExp(
  "^(?![\\s\\S]*(authori[sz]ed|sponsor|eligible|legally|willing|able to|relocat|startup|years of|experience|industry))[\\s\\S]*(" +
    [
      // ever worked for / been employed by the company (or "currently employed by X")
      "(ever|previously|formerly|before|past|currently|now)\\b[\\s\\S]*\\b(worked|work|employed|employee|contractor|intern|consultant)\\b[\\s\\S]*\\b(for|at|by)\\b",
      "(former|previous|current)[\\s_-]*(employee|contractor|worker)|employee of|employed by|worked (for|at)",
      // government employees and officials
      "(employee|official|employed)[\\s\\S]*(government|governmental)|(government|governmental)[\\s\\S]*(employee|official|employed)",
      // relatives or family who work there
      "(family|relative|spouse|sibling)[\\s\\S]*(employ|work)",
      "customer (of|or)|client of",
      "suspen(ded|sion)|debar|ineligible",
      "(citizen|resident)[\\s\\S]*(cuba|syria|iran|north korea|crimea|sanction|embargo)",
      "graduate of[\\s\\S]*program|alumni of",
      "non[\\s-]*compete|non[\\s-]*solicit|restrictive covenant|conflict of interest",
      "(ever|previously)[\\s\\S]*(applied|interviewed)|(applied|interviewed)[\\s\\S]*(before|previously|in the past)"
    ].join("|") +
    ")",
  "i"
);

const RULES = [
  { key: "answerNo", re: NO_QUESTIONS },
  // ---- NAME RULES (order matters, keep them in this order) ----
  // 1. Skip other people's names and usernames.
  { key: "SKIP", re: /\breference|\breferr(al|er|ed)|emergency|manager|supervisor|spouse|parent|guardian|recruiter|friend|colleague|relative|beneficiary|next[\s_-]*of[\s_-]*kin|user[\s_-]*name|username|login|file[\s_-]*name/i },
  // 2. Skip title, suffix and pronoun fields.
  { key: "SKIP", re: /prefix|suffix|salutation|honorific|pronoun/i },
  // Other social links and questions we never answer (so "Twitter URL" is not mistaken for your website).
  { key: "SKIP", re: /twitter|facebook|instagram|youtube|tiktok|\bx\.com|hispanic|latino|latinx/i },
  // 3. One box that wants both names: "First and last name", "First Last", "Full name (first and last)".
  { key: "nameFull", re: /(first|given|forename)[\s_.-]*(name)?[\s_.,&+\/()-]*(and|&|\+|\/|,)?[\s_.,&+\/()-]*(last|sur|family)/i },
  // 4. One box that wants "Last, First".
  { key: "nameLastFirst", re: /(last|sur|family)[\s_.-]*(name)?[\s_.,&+\/()-]*(and|&|\+|\/|,)?[\s_.,&+\/()-]*(first|given|forename)/i },
  // 5. Preferred name variants.
  { key: "nameLast", re: /preferred[\s_-]*(last|family|sur)/i },
  { key: "namePreferred", re: /preferred[\s_-]*(first|given|name)|nick[\s_-]*name|goes[\s_-]*by|known[\s_-]*as|chosen[\s_-]*(first|name)|what[\s_-]*(should|do)[\s_-]*(we|you)[\s_-]*call/i },
  // 6. Middle name or initial (left blank if you have no middleName in PROFILE).
  { key: "nameMiddle", re: /middle|additional[\s_-]*name|\bmi\b/i },
  // 7. Separate first and last boxes. "Legal first name" lands here too, and is switched to your legal name automatically.
  { key: "nameFirst", re: /first[\s_-]*(name|nm)|given[\s_-]*name|^given\b|forename|fname|name[\s_.()-]*first|^first[\s*:]*$/i },
  { key: "nameLast", re: /last[\s_-]*(name|nm)|family[\s_-]*name|sur[\s_-]*name|(?<!l)lname|name[\s_.()-]*last|^last[\s*:]*$|second[\s_-]*name/i },
  // ---- Questions that contain another field's keyword. Keep these above the general rules below. ----
  // "...require employer sponsorship..." would otherwise match the employer rule.
  { key: "needsSponsorship", re: /sponsor/i },
  // "...authorized to work in the country in which you are applying" would otherwise match the country rule.
  { key: "workAuthorized", re: /authorized|authorised|legally[\s_-]*(able|eligible)|right[\s_-]*to[\s_-]*work/i },
  // "Where did you complete your undergraduate degree?" would otherwise match the degree rule.
  { key: "undergradSchool", re: /(undergrad(uate)?|bachelor'?s?)[\s\S]*(degree|school|university|college|institution)|where[\s\S]*(complete|earn|receive|get|obtain|attend)[\s\S]*(undergrad|bachelor)/i },
  { key: "testScores", re: /standardi[sz]ed[\s_-]*tests?|\b(sat|act)\b[\s_/-]*((and|or)[\s_/-]*)?((sat|act)\b[\s_-]*)?scores?|test[\s_-]*scores?/i },
  { key: "email", re: /e[\s_-]*mail/i },
  { key: "phone", re: /phone|mobile|\btel\b|telephone/i },
  { key: "linkedin", re: /linked[\s_-]*in/i },
  { key: "github", re: /github/i },
  { key: "website", re: /website|portfolio|personal[\s_-]*(site|url)|\burl\b/i },
  { key: "zip", re: /zip|post(al)?[\s_-]*code/i },
  { key: "stateAbbr", re: /^state[\s_-]*(code|abbr)/i },
  { key: "state", re: /\bstate\b|province|region/i },
  // Single "Current location" box, filled as "City, State" (or set PROFILE.location to override).
  { key: "location", re: /^(current[\s_-]*)?location\b|current[\s_-]*location|location[\s_-]*input|where[\s_-]*(are|do)[\s_-]*you[\s_-]*(located|based|live)|city[\s,\/&-]*(and[\s_-]*)?state/i },
  { key: "city", re: /\bcity\b|town/i },
  { key: "country", re: /country/i },
  // Short field names only, so a long question like "what address will you work from?" is not filled.
  { key: "address", re: /^\W*(street |home |mailing |current )?address( line)?( ?1)?\W*(optional)?\W*$|address[\s_-]*line[\s_-]*1|address1|street[\s_-]*address|^street$/i },
  // Education date boxes. `edu: true` means only inside an education block, so work-experience dates are left alone.
  { key: "eduStartMonth", edu: true, re: /(start|from)[\s_-]*(date)?[\s_-]*month/i },
  { key: "eduStartYear", edu: true, re: /(start|from)[\s_-]*(date)?[\s_-]*year/i },
  { key: "eduEndMonth", edu: true, re: /(end|\bto\b)[\s_-]*(date)?[\s_-]*month/i },
  { key: "eduEndYear", edu: true, re: /(end|\bto\b)[\s_-]*(date)?[\s_-]*year/i },
  { key: "school", re: /school|university|college|institution/i },
  { key: "degree", re: /degree/i },
  { key: "major", re: /major|field[\s_-]*of[\s_-]*study|discipline/i },
  // Full graduation date (uses PROFILE.gradDate), checked before the year only rule.
  { key: "gradDate", re: /grad(uation)?[\s_-]*date|date[\s_-]*of[\s_-]*graduation|expected[\s_-]*graduation(?![\s_-]*year)/i },
  { key: "gradYear", re: /grad(uation)?[\s_-]*(year|date)|year[\s_-]*of[\s_-]*graduation/i },
  { key: "gpa", re: /\bgpa\b/i },
  { key: "currentCompany", re: /current[\s_-]*(company|employer)|employer/i },
  { key: "currentTitle", re: /current[\s_-]*(title|position)|job[\s_-]*title/i },
  { key: "veteran", re: /veteran/i },
  { key: "disability", re: /disabilit/i },
  { key: "gender", re: /gender/i },
  { key: "race", re: /\brace\b|ethnic/i },
  // A single "Name" box. Last, and anchored, so "company name" or "school name" never land here.
  // Matches: Name, Full name, Legal name, Your name, Candidate name, Applicant name, Name *, Name (required)
  { key: "nameFull", re: /^(?:(?:your|my|the|full|legal|complete|candidate|applicant|candidate's|applicant's|government|official)[\s_-]*)*name(?!\s*of\b)(?:[\s*:(\[].*)?$/i },
  // "Name as it appears on your passport / ID"
  { key: "nameFull", re: /^legal\s*[(\[]?\s*name/i },
  { key: "nameFull", re: /name.{0,30}(passport|\bid\b|license|identification|government)|(passport|government)[\s_-]*name/i }
];

// Used by name rules: if any of the field's label text matches this, the legal name is used.
// Otherwise your everyday name is used (firstName / lastName / fullName above).
const LEGAL_RE = /legal|government|passport|birth[\s_-]*certificate|on (your |the )?(id|passport|driver|license|identification)|official[\s_-]*name/i;

// Floating "Autofill" button on pages that look like job applications.
// Set to false if you only want the popup button and keyboard shortcut.
const SHOW_FLOATING_BUTTON = true;
