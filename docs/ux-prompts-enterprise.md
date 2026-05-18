# PatronAI Enterprise — Stitch UI Prompts
# 8 screens. Copy each prompt block into Stitch one at a time.
# Palette locked: bg #F8FAFC · card #FFFFFF · border #E2E8F0 · text #0F172A
#                 accent #4F46E5 · GHOST #DC2626 · NOISE #6B7280 · CLEAN #16A34A
# Font: Inter. Reference style: Datadog / Grafana Cloud.

---

## PROMPT 1 — App Shell (Global Layout)

Design a professional enterprise SaaS application shell for a product called PatronAI —
an AI governance and shadow AI detection platform for enterprise security teams.

LAYOUT: Three-column grid — left sidebar (220px fixed) + main content (flex-grow) +
optional right chat panel (380px, hidden by default, slides in on demand).

LEFT SIDEBAR:
- Top: PatronAI logo (text wordmark "PatronAI" in bold Inter, indigo #4F46E5, 18px) with a
  small shield icon to its left
- Navigation items with icons (Lucide-style, 16px): Dashboard · Reports · Users · Settings
  Each item: 40px tall, 12px horizontal padding, rounded-md hover state (#F1F5F9), active
  state has left border 2px solid #4F46E5 and background #EEF2FF
- Role badge below nav: pill label showing current role (EXEC · MANAGER · SUPPORT · ADMIN)
  in muted text, 10px, uppercase, letter-spacing 0.08em
- Bottom of sidebar: avatar circle (32px) + user email (12px, truncated) + logout icon

TOP HEADER BAR:
- Height 56px, background #FFFFFF, bottom border 1px #E2E8F0
- Left: page title (current screen name, 16px semibold, #0F172A)
- Right: notification bell icon (badge with count) + "Ask AI" button (ghost button, indigo
  border, opens the right chat panel) + user avatar

MAIN CONTENT AREA:
- Background #F8FAFC, padding 24px
- No internal scrollbars on outer shell — content area scrolls independently

RIGHT CHAT PANEL (closed state shows a thin 40px tab with chat bubble icon on right edge):
- When open: 380px wide, background #FFFFFF, left border 1px #E2E8F0, slides in with
  ease-in-out transition 200ms

Overall aesthetic: clean, spacious, high information density without feeling cluttered.
Inspired by Grafana Cloud and Linear.app. No gradients. Subtle shadows (shadow-sm) on cards.

---

## PROMPT 2 — Executive Dashboard

Design the Executive Dashboard screen for PatronAI Enterprise, an AI governance platform.
This view is for a CISO or Security VP who needs an instant read on unauthorized AI use
across their organisation.

Use the app shell from Prompt 1. Fill the main content area with:

KPI ROW (6 cards, horizontal, equal width, gap 16px):
- Card 1: "Ghost Signals" — large number in red #DC2626, bold 32px. Label below in 11px
  uppercase #6B7280. Delta chip below: "+2 vs yesterday" in red if positive, green if negative.
  This is the most important card — give it a subtle left border 3px solid #DC2626.
- Card 2: "AI Findings" — number in #0F172A, 32px bold. Delta chip.
- Card 3: "High Severity" — number in #D97706 (amber), 32px bold. Delta chip.
- Card 4: "AI Providers Detected" — number, no delta.
- Card 5: "Categories Found" — number, no delta.
- Card 6: "Alerts Fired Today" — number. Delta chip.
All cards: white background, border #E2E8F0, border-radius 8px, padding 20px, shadow-sm.
Hover state: border color shifts to #4F46E5.

SIGNAL FILTER TOGGLE (below KPI row, aligned right):
Toggle switch labeled "Ghost signals only" — default ON. When ON, a small red pill badge
"FILTERED: GHOST" appears inline. When OFF, pill shows "ALL SIGNALS" in grey.

TAB BAR (below toggle):
Three tabs: "AI Landscape" (default active) · "Risk Heatmap" · "Data Exposure"
Active tab: bottom border 2px solid #4F46E5, text #4F46E5. Inactive: #6B7280.

TAB CONTENT — AI Landscape:
Left 65%: Bubble/treemap chart showing AI tools detected. Each bubble = one AI provider
(e.g. OpenAI, Cursor, Ollama, GitHub Copilot). Bubble size = number of events. Color =
signal_class: GHOST red #DC2626, NOISE grey #6B7280, CLEAN green #16A34A. White background
card, border #E2E8F0, padding 20px.
Right 35%: "Top Users" vertical list — avatar initial + email + event count + signal_class
pill. Each row 48px. Clicking a row highlights the bubble chart entry.

TAB CONTENT — Risk Heatmap (show as secondary, don't detail):
Category × Severity matrix. Rows = AI tool categories. Columns = CRITICAL/HIGH/MEDIUM/LOW.
Cell color from white to deep red based on count. Clicking a cell opens a drill-down drawer.

TAB CONTENT — Data Exposure (show as secondary):
Sankey flow diagram: Department → AI Provider. Nodes colored by volume.

---

## PROMPT 3 — Manager Dashboard

Design the Manager Dashboard screen for PatronAI Enterprise.
This view is for a SecOps Manager or Platform Admin who triages shadow AI findings daily.

Use the app shell from Prompt 1. Fill the main content area with:

SIGNAL SUMMARY ROW (4 metric chips, horizontal, compact — 48px tall total):
Inline horizontal strip, background #FFFFFF, border #E2E8F0, border-radius 8px, padding
12px 20px. Four chips separated by vertical dividers:
"🔴 Ghost  4" · "⚫ Noise  87" · "✅ No Issue  210" · "◻ Unclassified  3"
Ghost count in red #DC2626. Noise in #6B7280. No Issue in #16A34A. Unclassified in #94A3B8.
Numbers bold 18px. Labels 11px uppercase muted.

SIGNAL FILTER RADIO (below summary strip, full width, pill-style):
Three pill buttons in a row: "🔴 Ghost only" (default selected, background #FEF2F2 border
#DC2626 text #DC2626) · "📋 All signals" · "⚫ Noise only".
Selected state: filled pill. Unselected: ghost button. Gap 8px between pills.

FILTER CAPTION (below radio, right-aligned):
Small text 12px #6B7280: "Showing 4 of 304 events · Ghost only"

HORIZONTAL RULE then TAB BAR:
Five tabs: "INVENTORY" · "RISKS" (default active) · "LOG VIEW" · "PIPELINE" · "AI INVENTORY"
Style: same as Prompt 2 tab bar.

TAB CONTENT — RISKS (default, show in full detail):
Header row: "OPEN ALERTS — 4 ITEMS" in 11px uppercase #6B7280 letter-spacing 0.1em.
Toggle (right-aligned): "Grouped view" switch, default ON.

GROUPED VIEW (when toggle ON):
Category accordion cards. Each card:
- Card header: category icon (shield/code/browser) + category name bold + event count badge
  (red if GHOST) + "AUTHORIZE ALL" button (ghost indigo button, 28px tall) on right
- Expanded content: table rows with columns: signal_class pill · domain/process · owner ·
  persistence days · occurrences · last seen · action icons (resolve ✓ / escalate ↑)
- signal_class pill: "GHOST" red pill, "NOISE" grey pill, "NO ISSUE" green pill

FLAT VIEW (when toggle OFF):
Datadog-style table. Columns: SIGNAL CLASS · TIME · SEVERITY · PROVIDER · OWNER ·
DEPARTMENT · SOURCE · OUTCOME · ACTIONS.
SIGNAL CLASS column: coloured pill badges. Severity: coloured text.
Row hover: #F8FAFC background. Row selection: #EEF2FF background with indigo left accent.
Action buttons below table: "✓ Mark Resolved" · "↑ Escalate" · "✉ Send Alert Email"
All ghost-style buttons, 36px tall.

---

## PROMPT 4 — Support Dashboard

Design the Support Team Dashboard screen for PatronAI Enterprise.
This view is for security operations engineers who investigate and triage all signal types.

Use the app shell from Prompt 1. Fill the main content area with:

HEADER STRIP:
Small label "SUPPORT TEAM VIEW" — 10px uppercase, #6B7280, letter-spacing 0.12em. Below it
the signal summary row (same as Prompt 3 — 4 metric chips in a horizontal card).

SIGNAL FILTER RADIO (4 options, pill style):
"📋 All signals" (default selected) · "🔴 Ghost only" · "⚫ Noise only" · "✅ No Issue"
Selected: filled pill. Unselected: ghost button. Same color logic as Prompt 3.

FILTER CAPTION: "Showing 304 of 304 events · All signals"

HORIZONTAL DIVIDER then EIGHT-TAB BAR:
RULES · CODE SIGNALS · COVERAGE · HEALTH · LOGS · RISKS · PIPELINE · AGENT FLEET
Tab bar horizontal scroll if viewport is narrow. Active tab has indigo underline.

TAB CONTENT — HEALTH (show as active/default for this prompt):
Two-row grid of metric cards:
Row 1 (4 cards): Heartbeats Today · Git Diff Signals Today · Code Signals Today · Dedup Rate
Row 2 (4 cards): Files Processed · Total Events Processed · Alerts Fired · Last Pipeline Run
Each card: white, border #E2E8F0, padding 16px, number 24px bold, label 11px muted.
Some cards clickable (show blue hover border) — clicking expands an inline drill table below.

TAB CONTENT — CODE SIGNALS (show as secondary panel below Health, partially visible):
Table header: "CODE SIGNAL QUEUE" in 11px uppercase.
Columns: TIME · DEVICE · OWNER · REPO · SNIPPET PREVIEW · STATUS
Status badges: "PENDING TRIAGE" in amber · "RESOLVED" in green.
Owner column: hyperlinks in indigo.

TAB CONTENT — AGENT FLEET (show as tertiary, only icon visible in tab bar):

---

## PROMPT 5 — Reports Screen

Design the Reports screen for PatronAI Enterprise.
Users generate and download compliance and operational reports.

Use the app shell from Prompt 1. Fill the main content area with:

PAGE HEADER:
"Reports" h1 20px bold #0F172A. Subtitle: "Generate compliance and operational reports for
your AI governance programme." 13px #6B7280.

REPORT CARDS GRID (2 columns × 4 rows, gap 16px):
Each card: white background, border #E2E8F0, border-radius 8px, padding 24px, shadow-sm.
Card structure:
- Top: icon (Lucide, 24px, indigo #4F46E5) + report name (16px semibold) + description
  (13px #6B7280, 2 lines max)
- Middle: Date range selector — "Last 7 days" default, dropdown options: 7d / 30d / 90d /
  Custom range (shows date pickers). Compact, 32px tall select.
- Bottom: Two action buttons side by side — "Generate PDF" (solid indigo, 32px) and
  "Export CSV" (ghost indigo, 32px)

The 7 report types:
1. Executive Summary — "Board-ready AI risk overview. Ghost signal trends, top offenders,
   compliance posture." — icon: bar chart
2. AI Inventory — "Complete catalogue of AI tools detected across your fleet." — icon: grid
3. User Activity — "Per-user AI usage breakdown with risk scoring." — icon: user
4. Incidents — "All HIGH/CRITICAL findings with timeline and resolution status." — icon: alert triangle
5. Fleet Health — "Agent coverage, heartbeat status, scan interval compliance." — icon: server
6. Compliance Report — "NIST/SOC2/ISO27001 control mapping for detected AI activity." — icon: shield check
7. Shadow AI Report — "Ghost signal deep-dive: persistent unauthorised AI use by category,
   user, and department." — icon: eye

RECENT REPORTS TABLE (below the cards, full width):
Title: "RECENT REPORTS" in 11px uppercase #6B7280.
Columns: REPORT TYPE · GENERATED BY · DATE · TIME RANGE · FORMAT · ACTIONS
Actions column: "Download" link in indigo + "Delete" icon in grey.
Zebra striping: even rows #F8FAFC. 40px row height.

---

## PROMPT 6 — Chat Side Panel

Design the AI chat side panel for PatronAI Enterprise.
This panel slides in from the right side of any dashboard screen when the user clicks
"Ask AI" in the top header bar.

PANEL DIMENSIONS: 380px wide, full viewport height, white background, left border
1px solid #E2E8F0, box-shadow -4px 0 16px rgba(0,0,0,0.08).

PANEL HEADER (56px, border-bottom 1px #E2E8F0):
Left: chat bubble icon (indigo) + "Ask PatronAI" text 14px semibold.
Right: close × icon button (grey, 32px tap target).

CONTEXT CHIP (below header, 12px padding):
Horizontal strip showing current dashboard context:
"Context: Manager View · Ghost only · 4 events" — small pill with indigo border, 11px text.
Below it: "PatronAI has access to your current filtered event view." in 11px #6B7280 italic.

MESSAGE AREA (flex-grow, overflow-y scroll, padding 16px, background #F8FAFC):
Messages in chat bubble style:
USER messages: right-aligned, background #4F46E5, text white, border-radius 16px 16px 4px 16px,
max-width 75%, padding 10px 14px, 13px text.
AI messages: left-aligned, background #FFFFFF, border 1px #E2E8F0, border-radius 16px 16px
16px 4px, max-width 85%, padding 12px 14px, 13px text #0F172A.
AI message footer: timestamp 10px #94A3B8. Below footer: source citation chips
(e.g. "john@co.com · 3 ghost events") in small grey pills.
Typing indicator: three animated dots in an AI bubble.

SUGGESTION CHIPS (above input, when conversation is empty):
4 pill buttons in a 2×2 grid, grey border, 11px text, 28px tall:
"Who has the most ghost signals?" · "Show me top offenders this week"
"What AI tools are in use?" · "Generate shadow AI report"

INPUT AREA (fixed bottom, padding 12px, border-top 1px #E2E8F0, background #FFFFFF):
Text input: full width, border 1px #E2E8F0, border-radius 8px, 40px tall, 13px text,
placeholder "Ask about your AI findings…". Focus state: border #4F46E5, box-shadow
0 0 0 3px #EEF2FF.
Send button: right-side of input row, solid indigo circle button 36px, paper-plane icon white.
Below input: "Powered by PatronAI · context-aware" in 10px #94A3B8 centered.

---

## PROMPT 7 — Onboarding: User Signup

Design the user signup and onboarding flow for PatronAI Enterprise.
This is a standalone full-page flow (no sidebar), shown to a new customer's admin user.

LAYOUT: Centered single column, max-width 480px, vertically centered on page.
Background: #F8FAFC full bleed. Logo at top center: PatronAI wordmark in indigo 24px bold
with shield icon. Below logo: "Enterprise Edition" pill badge in indigo outline.

STEP PROGRESS INDICATOR (horizontal, 3 steps):
Step 1: "Company" · Step 2: "Account" · Step 3: "Configure"
Active step: filled indigo circle with step number, label bold indigo.
Completed step: filled indigo circle with checkmark icon.
Inactive step: grey circle outline, grey label.
Connecting lines between circles, grey → indigo when completed.

STEP 1 CARD (white, border #E2E8F0, border-radius 12px, padding 32px, shadow-md):
Title: "Tell us about your company" 20px semibold #0F172A.
Subtitle: "This helps us configure your AI governance scope." 13px #6B7280.
Fields (label above input, 14px labels, 40px input height, border #E2E8F0 inputs,
focus border #4F46E5, border-radius 6px):
- Company name (required)
- Primary domain (e.g. acme.com) — helper text "Used to scope AI activity detection"
- Company size — select dropdown: 1-50 · 51-200 · 201-1000 · 1000+
- Industry — select: Technology · Finance · Healthcare · Retail · Other
"Continue →" button: full width, solid indigo, 44px tall, 15px semibold, border-radius 8px.
Bottom link: "Already have an account? Sign in" in indigo, 13px.

STEP 2 CARD (same card style):
Title: "Create your admin account" 20px semibold.
SSO OPTION BANNER at top: white card with indigo left border, 12px text:
"Your organisation uses SSO. Click below to authenticate via your identity provider instead."
"Sign in with SSO →" indigo ghost button.
Divider: "— or create a password account —"
Fields: Work email · Password · Confirm password
Password strength indicator bar below password field (red→amber→green).
Checkbox: "I agree to the Terms of Service and Privacy Policy" (required)
"Create Account →" button: full width solid indigo 44px.

STEP 3 CARD:
Title: "Configure your platform" 20px semibold.
Subtitle: "Your IT team will need these details. You can update these later in Settings."
Fields:
- S3 Bucket name (pre-filled: "patronai") — helper: "Where PatronAI stores findings data"
- AWS Region — select dropdown (default us-east-1)
- Alert email recipients — chip input, add multiple emails
- Authorised AI domains — chip input, add approved domains (e.g. api.openai.com)
"Launch PatronAI →" button: full width solid indigo 44px. On click → redirect to dashboard.
Below button: "Your first scan will begin automatically." 12px #6B7280.

---

## PROMPT 8 — Platform Configuration (Settings)

Design the Platform Configuration / Settings screen for PatronAI Enterprise.
This screen is for admins to manage the scanner, alerts, identity resolution,
provider allow-lists, users, and branding.

Use the app shell from Prompt 1. Fill the main content area with:

PAGE HEADER:
"Platform Settings" h1 20px bold. Subtitle: "Configure scanner behaviour, integrations,
and user management for your PatronAI deployment." 13px #6B7280.

SETTINGS TAB BAR (7 tabs, icon + label):
⚙ Scanner · 🔔 Alerts · 👤 Identity · 🌐 Providers · 🚀 Deploy Agents · 🎨 Branding · 👥 Users

TAB CONTENT — SCANNER (show as active/default):
Two-column layout: left 60% form, right 40% live status card.
LEFT FORM — white card, border #E2E8F0, padding 24px, title "Scanner Configuration" 14px semibold:
Form groups (each group: label 13px #374151 + input or select + helper text 11px #6B7280):
- S3 Bucket: text input (pre-filled "patronai")
- AWS Region: select
- Scan Interval: select (5 min / 15 min / 30 min / 60 min)
- Stale Cycle Threshold: number input, helper "Scans before auto-resolving unseen signatures"
- Ghost Min Occurrences: number input, helper "Occurrences before classifying as Ghost signal"
- Ghost Min Persistence (days): decimal input
Save button: solid indigo "Save Scanner Config" 36px.

RIGHT CARD — "Scanner Health" white card, border-left 3px solid #16A34A:
Status row: green dot + "RUNNING" badge. Last scan: "2 minutes ago".
Metric rows: Findings today · Signatures compacted · Auto-resolved today · LLM enriched.
Each metric: label 12px #6B7280 + value 18px bold #0F172A side by side.
"Trigger Manual Scan" ghost indigo button at bottom.

TAB CONTENT — ALERTS (show as secondary, partially visible):
Fields: Alert email recipients (chip input) · SNS ARN · Trinity Webhook URL ·
Prism7 email (for GHOST escalations). Test buttons alongside each field.

TAB CONTENT — USERS (show as tertiary, icon only visible in tab bar):
Table: EMAIL · ROLE · LAST LOGIN · STATUS · ACTIONS.
Role badges: ADMIN (indigo) · EXEC (purple) · MANAGER (blue) · SUPPORT (grey).
Status badges: ACTIVE (green) · INVITED (amber) · SUSPENDED (red).
"Invite User" button top right, solid indigo.

---
# END OF PROMPTS
# Stitch workflow:
# 1. Paste Prompt 1 (App Shell) first — establishes the design system
# 2. Paste Prompts 2-8 referencing "app shell from Prompt 1"
# 3. Export each screen as clean HTML/CSS
# 4. Hand HTML back for NiceGUI conversion
