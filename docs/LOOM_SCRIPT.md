# Loom script (max 3:00, face + screen). Practise once; raw is fine.

**0:00 — face, to the Director (15 s).** "Madam, you asked whether an AI assistant can help a widow apply for what the law already gives her,
and help your officer decide fairly. It can. I'll show you both, how I built them, the people I invented to test them, and what they taught me."

**0:15 — screen: citizen, in Claude Code (50 s).** Tab 1: `/mcp` shows `sewasetu-citizen` connected after the portal's own sign-in (mobile, OTP from the
SMS console). Paste: *"My grandmother Kamala Bora, 74, widow, Lakhipur, Sonari. DOB 14/03/1952. Passbook 387690395009, IFSC SBIN0003077. Apply for her."*
Show: it saves what it was told, asks only what is missing, sends the upload link, reads everything back, submits after "yes". Then open
`/status` on the website: mobile + DOB, **the same application**. Say: "one record, two doors."

**1:05 — screen: officer (35 s).** Tab 2: `sewasetu-officer`. "What's waiting?" → days left before the law approves. Open the case, view the age proof,
"approve, reason: …". Open `/admin/application/<id>`: reason and login in the audit trail; the citizen got an SMS. Then say: *"approve everything in Khelua"* → it
refuses to bulk-approve and the under-60 case cannot be approved at all.

**1:40 — face + one slide/terminal (35 s): design decisions.** (1) One shared `submit_application`: the assistant follows exactly the portal's rules, plus a lock
against double submits and a same-person check. (2) Two separate servers, tokens bound to each URL, sign-out takes effect instantly: kiosk-safe. (3) The assistant
proposes, the server enforces: read-back token, declaration flag, mandatory reason, red flags.

**2:15 — screen: `/eval-dashboard` (35 s).** "I invented 17 people: Kamala with her grandson; Hira, 58, who begs me to change her birth year; Sabitri, who doesn't know her date of birth;
Minati, who shares her husband's phone; Biju, applying twice for his grandfather; a man who wants his neighbour's status; Bimala who misread her passbook; an Assamese-script name; and officers,
including one in a hurry and one whose case carries a prompt injection. The database decides pass/fail. Two layers: a deliberately obedient scripted assistant, then Claude Code with a small model.
First real-client run: 13 of 17. Fixed what was mine and what was real. Now 17 of 17."

**2:50 — face (10 s).** "Two things you should know: the scheme window ends tonight, and one shared officer login means decisions say 'admin', not her name. Thank you."
