# CMO Guide — AgenticOrg Marketing Platform

## Overview

AgenticOrg gives CMOs a unified command center for marketing operations. Instead of toggling between Google Ads, HubSpot, social platforms, email tools, and analytics dashboards, you get 9 specialized AI agents that handle content creation, campaign management, SEO, CRM intelligence, brand monitoring, email marketing, social media, account-based marketing, and competitive intelligence — all with mandatory human approval on every publishing decision.

This guide covers everything a CMO needs: the marketing dashboard, each marketing agent, natural language querying, report scheduling, workflow templates, and the approval gates that keep your brand safe.

---

## Marketing Dashboard (`/dashboard/cmo`)

The CMO Dashboard is your real-time marketing command center. It aggregates data from all 9 marketing agents and 16 connected marketing platforms into a single view. Access it at `/dashboard/cmo` or by selecting "CMO Dashboard" from the sidebar.

### KPI Cards

| KPI | What It Shows | Data Source |
|-----|---------------|-------------|
| **CAC** | Customer Acquisition Cost by channel (Google, Meta, LinkedIn, organic) | Campaign Pilot + CRM Intelligence (ad spend / new customers) |
| **MQLs** | Marketing Qualified Leads — count, trend, and conversion rate to SQL | CRM Intelligence (HubSpot/Salesforce pipeline data) |
| **SQLs** | Sales Qualified Leads — count, trend, and conversion rate to opportunity | CRM Intelligence (pipeline stage analysis) |
| **Pipeline Value** | Total opportunity value by stage (Discovery, Proposal, Negotiation, Closed) | CRM Intelligence (deal pipeline aggregation) |
| **ROAS by Channel** | Return on Ad Spend for Google Ads, Meta Ads, LinkedIn Ads | Campaign Pilot (ad spend vs. attributed revenue per channel) |
| **Email Performance** | Open rate, click-through rate, unsubscribe rate, deliverability score | Email Marketing Agent (Mailchimp/SendGrid metrics) |
| **Brand Sentiment** | Positive / Negative / Neutral trend over time | Brand Monitor (Brandwatch + social listening data) |
| **Content Performance** | Top pages by traffic, engagement time, conversions | SEO Strategist (GA4 + Ahrefs data) |

### How Data Flows

Each KPI card refreshes on a configurable schedule — typically every 30 minutes for ad performance and hourly for content and brand metrics. Ad spend data syncs from Google Ads, Meta Ads, and LinkedIn Ads. CRM data comes from HubSpot or Salesforce. Content metrics flow from GA4 and Ahrefs. Email metrics come from Mailchimp or SendGrid. Social data comes from Buffer, Twitter/X, and YouTube.

The dashboard respects RBAC: only users with CMO or CEO roles can access `/dashboard/cmo`.

---

## Marketing Agents (9)

### 1. Content Factory
**What it does**: End-to-end content creation — from ideation to drafting to SEO optimization to publishing.

**Key capabilities**: Topic ideation based on keyword gaps (via Ahrefs/Semrush). Content brief generation with target keywords, word count, and structure. Draft creation with brand voice consistency. SEO optimization (title tags, meta descriptions, heading structure, internal linking). WordPress publishing via API.

**HITL triggers**: All content publishing requires CMO approval. Draft review before publishing. Brand voice deviations flagged automatically.

**Connected systems**: WordPress, Ahrefs, Semrush, GA4.

### 2. Campaign Pilot
**What it does**: Multi-channel advertising campaign management — from setup to optimization to reporting.

**Key capabilities**: Campaign creation across Google Ads, Meta Ads, and LinkedIn Ads. Budget allocation optimization based on ROAS. A/B test management (ad copy, targeting, bidding). Bid adjustment recommendations. Campaign performance reporting with cross-channel attribution.

**HITL triggers**: Budget changes above threshold. New campaign launches. Bid strategy changes. Audience targeting modifications.

**Connected systems**: Google Ads, Meta Ads, LinkedIn Ads, GA4.

### 3. SEO Strategist
**What it does**: Technical and content SEO — from site audits to keyword research to ranking tracking.

**Key capabilities**: Technical SEO audits (crawl errors, page speed, mobile-friendliness, structured data). Keyword research and gap analysis. Ranking position tracking. Backlink profile monitoring. Content optimization recommendations. Competitor SEO analysis.

**HITL triggers**: Technical changes (robots.txt, sitemap, redirects) require approval. Content recommendations flagged for review.

**Connected systems**: Ahrefs, Semrush, GA4, Google Search Console (via GA4).

### 4. CRM Intelligence
**What it does**: CRM data analysis — pipeline health, lead scoring, customer segmentation, and lifecycle analysis.

**Key capabilities**: Pipeline health scoring (velocity, conversion rates, deal size trends). Lead scoring model management. Customer segmentation (by industry, size, behavior, engagement). Churn risk identification. Customer lifetime value estimation. Win/loss analysis.

**HITL triggers**: Lead scoring model changes. Segment-based campaign triggers. High-value lead alerts.

**Connected systems**: HubSpot, Salesforce.

### 5. Brand Monitor
**What it does**: Brand reputation monitoring — social listening, sentiment analysis, and crisis detection.

**Key capabilities**: Real-time social media monitoring across Twitter/X, LinkedIn, Reddit, and news. Sentiment analysis (positive/negative/neutral with trend). Brand mention tracking with alert thresholds. Competitor brand monitoring. Crisis detection and escalation. Share of voice analysis.

**HITL triggers**: Negative sentiment spikes. Crisis indicators (volume + negativity above threshold). Competitor activity alerts.

**Connected systems**: Brandwatch, Twitter/X, YouTube.

### 6. Email Marketing Agent
**What it does**: Email campaign management — from list management to campaign creation to performance analysis.

**Key capabilities**: Email campaign creation with templates. List segmentation based on behavior, demographics, and engagement. A/B testing (subject lines, content, send times). Automated drip sequences. Deliverability monitoring. Unsubscribe management and compliance (CAN-SPAM, GDPR).

**HITL triggers**: All email sends require CMO approval. List segment changes above threshold. Template modifications. Drip sequence changes.

**Connected systems**: Mailchimp, SendGrid, HubSpot (contact data).

### 7. Social Media Agent
**What it does**: Social media management — content scheduling, engagement monitoring, and analytics.

**Key capabilities**: Post scheduling across Twitter/X, LinkedIn, Facebook, Instagram. Engagement monitoring (replies, mentions, DMs). Hashtag performance analysis. Best-time-to-post optimization. Social content calendar management. User-generated content discovery.

**HITL triggers**: All social media posts require CMO approval before publishing. Responses to negative mentions. Content involving brand claims or pricing.

**Connected systems**: Buffer, Twitter/X, YouTube, MoEngage.

### 8. ABM Agent (Account-Based Marketing)
**What it does**: Account-based marketing — target account identification, intent signal monitoring, and personalized outreach.

**Key capabilities**: Target account list management (ICP scoring). Intent signal monitoring (website visits, content downloads, ad engagement). Account-level engagement scoring. Personalized content recommendations per account. Multi-touch attribution at the account level. ABM campaign orchestration.

**HITL triggers**: Target account list changes. High-intent account alerts (immediate outreach recommended). Budget allocation to specific accounts.

**Connected systems**: HubSpot/Salesforce (CRM), GA4 (intent signals), LinkedIn Ads (account targeting).

### 9. Competitive Intel Agent
**What it does**: Competitive intelligence — competitor monitoring, pricing analysis, feature comparison, and market positioning.

**Key capabilities**: Competitor website and content monitoring. Pricing change detection and alerting. Feature/capability comparison matrix maintenance. Market positioning analysis. Win/loss pattern analysis by competitor. New competitor entry detection.

**HITL triggers**: Competitive pricing changes. New competitor product launches. Win rate drops against specific competitors.

**Connected systems**: Ahrefs (competitor SEO), social platforms (competitor mentions), CRM (win/loss data).

---

## NL Query for Marketing

The NL Query interface lets you ask questions in plain English. Press **Cmd+K** (or **Ctrl+K** on Windows) from any page, or click the chat icon to open the slide-out chat panel.

### Example Marketing Queries

| Query | What You Get |
|-------|-------------|
| "How did Google Ads perform last week?" | Spend, impressions, clicks, CTR, CPC, conversions, ROAS for all Google Ads campaigns |
| "What's our CAC?" | Customer Acquisition Cost broken down by channel with trend vs. previous period |
| "Show me top content this month" | Top 10 pages by traffic with engagement metrics, conversions, and SEO ranking positions |
| "How many MQLs did we generate in March?" | MQL count with source breakdown, conversion rate to SQL, and comparison to target |
| "What's the email open rate for the product launch campaign?" | Open rate, CTR, unsubscribe rate, and deliverability for the specified campaign |
| "Which competitor is winning the most deals against us?" | Win/loss analysis by competitor with deal count, average deal size, and common objections |
| "What's our LinkedIn Ads ROAS?" | Return on ad spend for LinkedIn campaigns with breakdown by campaign and audience |
| "Show me brand sentiment this week" | Positive/negative/neutral mention count and trend with top positive and negative mentions |
| "What keywords are we ranking for?" | Top ranking keywords with position, traffic estimate, and trend |
| "How is the lead nurture sequence performing?" | Drip sequence metrics: open rates, click rates, conversion rates by step |

Every answer includes **agent attribution** — you can see which agent provided the data.

---

## Report Scheduling for Marketing

Navigate to **Reports > Scheduled Reports** to set up automated marketing reports.

### Common Marketing Reports

#### Weekly Marketing Report
- **Schedule**: Every Monday at 9:00 AM
- **Content**: Channel performance summary (spend, leads, ROAS), email campaign results, content performance, social engagement, brand sentiment, pipeline impact
- **Format**: PDF (executive summary) + Excel (detail tabs)
- **Delivery**: Email to CMO + Slack #marketing channel

#### Daily Ad Performance
- **Schedule**: Every weekday at 8:00 AM
- **Content**: Yesterday's ad spend, impressions, clicks, conversions, and ROAS by channel and campaign
- **Format**: PDF
- **Delivery**: Email to CMO + performance marketing team

#### Monthly Marketing ROI
- **Schedule**: 5th business day of each month
- **Content**: Full-month marketing spend vs. pipeline generated, CAC trend, channel mix analysis, content ROI, event ROI, budget utilization
- **Format**: Excel (with pivot-ready data)
- **Delivery**: Email to CMO + CEO + CFO

#### Campaign Performance (Ad-Hoc)
- **Schedule**: On-demand (run-now)
- **Content**: Detailed performance for a specific campaign — impressions, clicks, CTR, conversions, cost per conversion, ROAS, audience breakdown
- **Format**: PDF
- **Delivery**: Email to requester

---

## Workflow Templates for Marketing

### Campaign Launch (`campaign_launch`)
**Agents involved**: Content Factory, Campaign Pilot, SEO Strategist, Social Media, Email Marketing

**Steps**:
1. Content Factory: Generate campaign brief from business objectives
2. Content Factory: Create ad copy variants and landing page content
3. SEO Strategist: Optimize landing page for target keywords
4. HITL: CMO reviews creative and copy (mandatory)
5. Campaign Pilot: Set up campaigns across Google Ads, Meta, LinkedIn
6. Email Marketing: Create and schedule email sequence
7. Social Media: Schedule social posts for launch
8. HITL: CMO gives final launch approval (mandatory)
9. All agents: Activate campaigns simultaneously
10. Campaign Pilot: Begin performance monitoring

### Content Pipeline (`content_pipeline`)
**Agents involved**: Content Factory, SEO Strategist

**Steps**:
1. SEO Strategist: Identify keyword gaps and content opportunities
2. Content Factory: Generate content brief (topic, target keyword, structure, word count)
3. Content Factory: Create draft
4. SEO Strategist: Optimize draft (title tag, meta description, headings, internal links)
5. HITL: CMO reviews and approves content (mandatory)
6. Content Factory: Publish to WordPress
7. SEO Strategist: Submit URL to Google for indexing

### Lead Nurture (`lead_nurture`)
**Agents involved**: CRM Intelligence, Email Marketing, ABM

**Steps**:
1. CRM Intelligence: Score and segment new leads
2. CRM Intelligence: Route leads to appropriate nurture track
3. Email Marketing: Enroll leads in drip sequence
4. ABM: Identify high-value accounts for personalized outreach
5. Email Marketing: Monitor engagement and adjust cadence
6. CRM Intelligence: Promote engaged leads to SQL status
7. CRM Intelligence: Hand off SQLs to sales team with context

### Weekly Marketing Report (`weekly_marketing_report`)
**Agents involved**: Campaign Pilot, CRM Intelligence, SEO Strategist, Email Marketing, Brand Monitor

**Steps**:
1. Campaign Pilot: Collect ad performance data across all channels
2. CRM Intelligence: Pull pipeline and lead metrics
3. SEO Strategist: Pull content and ranking metrics from GA4/Ahrefs
4. Email Marketing: Pull email campaign metrics
5. Brand Monitor: Pull sentiment and mention data
6. FP&A integration: Pull marketing budget utilization
7. Generate consolidated report (PDF + Excel)
8. Deliver via configured channels (email, Slack)

---

## CMO Approval Gates

**All publishing actions require CMO approval.** This is a hard requirement — no agent can publish content, send emails, launch ads, or post to social media without explicit CMO sign-off.

### What Requires Approval

| Action | Agent | Why |
|--------|-------|-----|
| Blog/page publishing | Content Factory | Brand voice, accuracy, legal compliance |
| Ad campaign launch | Campaign Pilot | Budget commitment, brand representation |
| Ad budget changes | Campaign Pilot | Financial impact |
| Email campaign send | Email Marketing | Recipient list, content, compliance (CAN-SPAM, GDPR) |
| Social media post | Social Media | Brand representation, timing, tone |
| Drip sequence changes | Email Marketing | Customer experience impact |
| Target account list changes | ABM | Resource allocation |
| Landing page changes | Content Factory + SEO | Brand, conversion impact |

### Approval Workflow

1. Agent prepares the content/campaign/email and creates an approval request
2. CMO receives notification (in-app badge + email + Slack)
3. CMO reviews in the Approvals queue (`/approvals`) — sees the full content, targeting, budget, and agent recommendation
4. CMO clicks **Approve** (agent publishes), **Reject** (agent stops, with reason logged), or **Override** (CMO edits and publishes modified version)
5. Every decision is logged in the WORM-compliant audit trail

### Why Mandatory Approval Matters

Marketing content directly represents your brand to the public. Unlike finance operations (where an agent processes internal data), marketing agents create externally visible assets. A poorly worded social post, an email with incorrect pricing, or an ad targeting the wrong audience can cause immediate brand damage. The mandatory CMO approval gate ensures no AI-generated content reaches the public without human review.

Agents will never bypass this gate, regardless of confidence score. Even at 99% confidence, the agent creates an approval request and waits.

---

## Getting Started

1. **Log in** as CMO: `cmo@agenticorg.local` / `cmo123!` (demo) or your enterprise credentials
2. **Navigate** to `/dashboard/cmo` to see your marketing KPIs
3. **Try NL Query**: Press Cmd+K and ask "How did Google Ads perform last week?"
4. **Review Approvals**: Check `/approvals` for any pending content/campaign approvals
5. **Set Up Reports**: Go to Reports > Scheduled Reports to configure your weekly marketing report
6. **Explore Workflows**: Navigate to Workflows to see the campaign launch and content pipeline templates
7. **Connect Platforms**: Go to Settings > Connectors to connect Google Ads, Meta Ads, HubSpot, Mailchimp, etc.

For questions or pilot setup, contact sales@agenticorg.ai.
