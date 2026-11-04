# Fundamental Analysis: 4062.T

**Note on the verified model:** the runtime model spec for 4062.T failed its validation checks (a period mismatch between the FY3/2028 scenario EPS and the FY3/2027 fair-value range). **The model is therefore reported as unavailable and no model-derived figures are used in this report.** All figures below come from the filing, the company presentation, broker commentary, internal notes, or my own arithmetic, and are labelled accordingly.

**Fiscal-year convention used throughout:** IBIDEN's fiscal year ends 31 March. The company labels the year ending March 2027 as "FY2026". To avoid ambiguity I use **FY3/2027** for the year ending March 2027, **FY3/2028** for the year ending March 2028, and so on. Company "FY2029"/"FY2030" therefore map to FY3/2030/FY3/2031.

---

## 1. Executive conclusion

- **Fundamental rating: Neutral.** Business quality and earnings momentum are strong; the share price already discounts a successful capacity ramp, and normalized free cash flow is negative.
- **Decision horizon:** 3–6 months (to ~end-April 2027).
- **Business direction:** strongly positive. Advanced IC package substrate demand is structurally short of supply; management expects industry SAP-area demand to reach 3.5x the CY2026 level by CY2030 against its own 2x capacity plan [PRES p.12].
- **Earnings direction:** positive but decelerating in growth rate. H1 FY3/2027 operating profit rose 84.6% to ¥60.1bn [TANSHIN p.1]; H2 guidance implies ¥72.9bn, and FY3/2028 guidance of ¥165bn operating profit is only +24% on FY3/2027 guidance [TANSHIN p.3].
- **Forecast versus guidance:** modestly above. I model FY3/2027 operating profit of ¥135–140bn versus guidance ¥133bn, and FY3/2028 of ¥175–195bn versus guidance ¥165bn.
- **Forecast versus consensus:** no material edge. Citi reports consensus FY3/2028 operating profit at ¥190bn [BROKERS ¶3]; my base of ¥185bn is effectively in line.
- **Cash-flow quality:** weak on a normalized basis. H1 FY3/2027 operating cash flow of ¥193.2bn includes a ¥118.8bn increase in customer advances [TANSHIN p.9]; excluding advances, operating cash flow was roughly ¥74bn, close to net profit plus depreciation.
- **Balance-sheet condition:** strong. Net cash of roughly ¥258bn at 30 September 2026 (cash ¥420.5bn less interest-bearing debt ¥162.1bn) [TANSHIN pp.4–5], equity ratio 53.5% [TANSHIN p.1].
- **Valuation:** demanding. At ¥8,045 the shares trade at ~51x FY3/2027 guidance EPS of ¥157.17 and ~38x my FY3/2028 diluted EPS of ~¥212, with negative normalized free cash flow. Fair-value range **¥6,800–8,000**.
- **Main catalyst (within horizon):** a formal decision and disclosure on Cell 7 / Cell 9 investment with customer terms, and a possible upward revision of the FY3/2031 ¥300bn operating-profit target, both targeted by management for within FY3/2027 [CALL ¶Q1/Q4; BROKERS ¶1].
- **Main risk:** ASP erosion and share dilution as a third AI-GPU substrate supplier qualifies, combined with a Cell 8 ramp that must carry FY3/2028 growth.
- **Principal limitation:** no company-disclosed consensus, no peer valuation data in the corpus, and the two internal notes carry unverified publication timing and contain internally inconsistent figures (flagged in Section 4).

---

## 2. Company and earnings structure

IBIDEN reports three segments [TANSHIN p.12]:

| Segment | H1 FY3/2027 revenue | H1 FY3/2027 OP | OP margin | Share of group OP |
|---|---|---|---|---|
| Electronics | ¥172.5bn | ¥50.0bn | 29.0% | 83.2% |
| Ceramics | ¥46.2bn | ¥5.4bn | 11.7% | 9.0% |
| Others | ¥43.8bn | ¥4.6bn | 10.5% | 7.7% |
| Consolidated | ¥262.5bn | ¥60.1bn | 22.9% | 100% |

**Electronics** is the economic engine: high-performance IC package substrates (build-up/ABF and SAP-based) for AI GPUs and ASICs, server CPUs, and high-end switch ICs. Revenue is a function of substrate *area* shipped (die size × layer count × processing steps), yield, price per unit area, and qualified capacity. Management's own framework is that unit volumes grow far more slowly than area demand: from CY2026 to CY2030, AI GPU/ASIC units grow 1.8x but SAP demand grows 5.4x, and general-purpose server CPU units grow 1.3x against 2.5x SAP demand [PRES p.12]. This is the core of the investment case — content per unit, not unit growth.

**Ceramics** supplies diesel particulate filter components, graphite products for nuclear power, and new battery-safety materials (NEB, in mass production and ramping) [CALL ¶Ceramics]. It is a mature, cyclical, automotive-exposed business.

**Others** includes construction and other non-core activities; intersegment revenue is large (¥16.8bn in H1) and eliminated [TANSHIN p.12].

**Geographic and customer exposure:** the filing does not disclose customer concentration. The business is nonetheless plainly concentrated in a small number of AI accelerator and server CPU customers, given that management describes the AI-GPU substrate market as a two-supplier system moving to three [CALL ¶Q2-A1]. This is an inference from management commentary, not a disclosed figure.

**Key operating variables:** qualified capacity (Cell 6 Gama, Cell 8 Ono), yield on increasingly complex structures (multi-core, embedded components, eventually glass core), ASP, product mix toward AI accelerators, and the depreciation that follows each capacity wave.

---

## 3. Latest results and operating drivers

**H1 FY3/2027 (April–September 2026), company-reported** [TANSHIN pp.1, 7, 12]:

| ¥bn | H1 FY3/2026 | H1 FY3/2027 | Change |
|---|---|---|---|
| Revenue | 195.5 | 262.5 | +34.3% |
| Gross profit | 67.9 | 97.7 | +43.9% |
| Gross margin | 34.7% | 37.2% | +2.5ppt |
| SG&A | 35.3 | 37.6 | +6.4% |
| Operating profit | 32.6 | 60.1 | +84.6% |
| Operating margin | 16.7% | 22.9% | +6.2ppt |
| Ordinary profit | 32.3 | 61.0 | +88.9% |
| Net profit (owners of parent) | 22.1 | 38.6 | +74.8% |
| Basic EPS (split-adjusted) | ¥39.52 | ¥68.63 | +73.7% |

**What drove it (company-confirmed):** management attributes the result to "favorable pricing and exceptionally strong demand for AI-related products" in Electronics, with Ceramics slightly ahead of plan on DPF backup parts and Others in line [CALL ¶H1]. Both revenue and operating profit were record first-half figures [CALL ¶H1].

**Operating leverage is the dominant mechanic.** Revenue rose ¥67.1bn while operating profit rose ¥27.5bn — an incremental operating margin of 41%. SG&A grew only 6.4% against 34.3% revenue growth, so essentially all of the margin expansion came from gross margin (+2.5ppt) and fixed-cost absorption. Within Electronics, revenue rose 52.0% and segment profit 96.7%, an incremental margin of 41.7% [TANSHIN p.12].

**Analyst interpretation (not company-stated):** the 41% incremental margin is consistent with a capacity-constrained, price-favourable environment in which incremental volume is allocated to the highest-specification products. It is not evidence of a permanent margin level — management explicitly assumes future ASP declines in its guidance [CALL ¶Q2-A2].

**Items below operating profit:** ordinary profit exceeded operating profit by ¥0.9bn, helped by interest income of ¥1.85bn and an FX gain of ¥0.68bn, partly offset by ¥1.67bn of depreciation on inactive assets [TANSHIN p.7]. Extraordinary items were a net ¥1.65bn loss, mainly retirement of non-current assets (¥1.61bn) [TANSHIN p.7]. The effective tax rate was 34.5% (¥20.5bn on ¥59.4bn pretax) [TANSHIN p.7].

**Cash flow:** operating cash flow of ¥193.2bn [TANSHIN p.9] is flattered by a ¥118.8bn increase in advances received [TANSHIN pp.5, 9]. Excluding that, operating cash flow was approximately ¥74bn — closely matching net profit of ¥38.6bn plus depreciation of ¥35.9bn. Capital expenditure was ¥48.9bn (PP&E ¥48.3bn plus intangibles ¥0.6bn) [TANSHIN p.10]. Reported free cash flow was therefore ~¥144bn, but normalized free cash flow was only ~¥25bn.

**Balance sheet:** total assets rose ¥167.2bn to ¥1,127.6bn, of which ¥150.1bn was current assets — driven by cash (+¥124.8bn) and, on the liability side, advances received (+¥118.8bn) [TANSHIN pp.4–5]. Construction in progress rose to ¥126.2bn from ¥111.8bn [TANSHIN p.4], confirming that a large share of the capex programme is not yet depreciating.

**Share count and dilution:** issued shares 567,387,652, treasury 1,859,388, giving 565,528,264 net shares at 30 September 2026 [TANSHIN p.3]. A subsequent event converted ¥11.5bn face of convertible bonds into 5,126,831 new shares between 1 and 21 October 2026 [TANSHIN p.13], taking net shares to approximately 570.7m. Remaining convertible bonds are ¥44.5bn of the ¥56.0bn outstanding at 30 September [TANSHIN p.5], with a post-split conversion price of ¥2,243.1 [TANSHIN p.14] — implying a further ~19.9m shares on full conversion, or ~590m fully diluted.

---

## 4. Management guidance and expectations

**FY3/2027 guidance (company "FY2026"), revised upward on 29 October 2026** [TANSHIN p.3]:

| ¥bn | Guidance | YoY |
|---|---|---|
| Revenue | 565.0 | +35.8% |
| Operating profit | 133.0 | +114.4% |
| Ordinary profit | 134.5 | +121.1% |
| Net profit | 89.0 | +39.7% |
| EPS (post-split) | ¥157.17 | — |

Implied H2: revenue ¥302.5bn, operating profit ¥72.9bn, operating margin 24.1%, net profit ¥50.4bn [CALL ¶H2; NOTE §3.1]. H2 operating profit therefore needs to rise 21% half-on-half from ¥60.1bn.

**Important comparability point:** the +39.7% net-profit growth in guidance is measured against an inflated base. FY3/2026 reported net profit of ¥63.7bn exceeded operating profit of ¥62.0bn despite a ~34% tax rate; vendor cash-flow data for FY3/2026 show a ¥40.3bn gain on investment securities alongside ¥57.5bn of investment sales [vendor cash-flow tool]. On a normalized basis FY3/2026 net profit was closer to ¥37bn, so underlying FY3/2027 net-profit growth is far higher than the headline 39.7%. This is my inference from vendor data, not a company disclosure.

**Medium-term plan** [BROKERS ¶1, ¶3, ¶6; CALL ¶Mid-term]:
- FY3/2028: revenue ¥650bn unchanged; operating profit raised from ¥150bn to **¥165bn**; Electronics operating profit ~¥144bn at ~31% margin.
- FY3/2031 (company "FY2030"): revenue ¥1.0tn and operating profit **¥300bn or more**, unchanged.
- Management confirmed the plan still embeds ASP declines (GS estimates ~¥10bn of FY3/2028 operating-profit drag) and excludes the newly announced ¥151bn Cell 6 investment, the spare space at Cell 8, and Cell 7/Cell 9 [BROKERS ¶1; CALL ¶Q2-A2].

**Consensus:** Citi reports consensus FY3/2028 operating profit at ¥190bn, i.e. ¥25bn above guidance, and characterises management's outlook as below market expectations [BROKERS ¶3]. No company-disclosed consensus is available in the corpus; this is a single broker's reference and should be treated as indicative only.

**Conflicts and data-quality issues I could not reconcile:**
1. JPM's note states FY3/2026 group revenue of "¥665.0bn" with operating profit of ¥133.0bn and Electronics revenue of ¥385.0bn with ¥141.0bn operating profit [BROKERS ¶6]. Revenue of ¥665bn contradicts the filing's ¥565bn guidance, and an Electronics margin of 36.6% contradicts the 29.0% actually delivered in H1. I treat these as transcription errors and do not use them.
2. JPM states the FY3/2028 plan was revised to ¥163.0bn operating profit [BROKERS ¶6]; the filing and the other brokers say ¥165.0bn [TANSHIN p.3; BROKERS ¶1, ¶3]. I use ¥165bn.
3. JPM's SAP demand series contains internal arithmetic errors (e.g. "13.5 for FY2027 (YoY +3.5%)" against 11.2 in FY2026, which is +20.5%) [BROKERS ¶6]. I use the presentation's index instead: 100 (CY2026) → 135 → 170 → 210 → 350 (CY2030) [PRES p.12].
4. The Cell 6 investment is described as "approximately ¥151 billion" in the filing [TANSHIN p.14] and as "JPY 150 billion" newly added in the presentation [PRES p.13]. I use ¥151bn.
5. Capex for FY3/2027 is "approximately ¥200.0bn" in the call note [CALL ¶Capex] and ¥210.0bn in JPM's note [BROKERS ¶6]. I use ¥200–210bn as a range.
6. The call note states an FY3/2027 payout ratio of "approximately 14%" [CALL ¶Shareholder returns]; DPS of ¥20 against guidance EPS of ¥157.17 is 12.7%. I use the computed 12.7%.

**Dividend:** interim ¥10.0 per share (raised from ¥7.5), full year ¥20.0, progressive policy to FY3/2031 with a ~20% payout guideline [PRES p.17; TANSHIN p.2]. Yield at ¥8,045 is 0.25%.

---

## 5. Independent forecast

I build from operating evidence rather than adopting guidance or consensus.

| ¥bn | FY3/2026A | FY3/2027E | FY3/2028E |
|---|---|---|---|
| Revenue | 416.2 | 565–575 (base 570) | 660–700 (base 680) |
| Operating profit | 62.0 | 135–140 (base 137) | 175–195 (base 185) |
| Operating margin | 14.9% | 24.0% | 27.2% |
| Net profit | 63.7 (incl. ~¥27bn after-tax securities gain) | 90–93 (base 91) | 120–130 (base 125) |
| Diluted EPS | — | ~¥157 | ~¥212 |

FY3/2026 actuals are vendor data reconciled to the filing's own growth rates (565.0/1.358 = 416.1; 133.0/2.144 = 62.0; 89.0/1.397 = 63.7) [TANSHIN p.3].

**FY3/2027 assumptions:**
- H1 delivered ¥60.1bn operating profit. H2 guidance of ¥72.9bn requires +21% half-on-half, which is below the +23.4% sequential step already achieved from Q1 to Q2 (¥26.9bn to ¥33.2bn) [NOTE §2.1]. I therefore assume a small beat: ¥137bn.
- Revenue of ¥570bn assumes H2 of ¥307.5bn, +17% half-on-half, consistent with continued AI substrate demand and mix improvement toward CPUs and switches [CALL ¶H2].
- Ceramics is the drag: H2 segment operating profit guided at only ¥2.1bn versus ¥5.4bn in H1, on automotive weakness [CALL ¶H2]. I do not assume a recovery.
- Tax rate 34%, consistent with the 34.5% H1 effective rate [TANSHIN p.7].

**FY3/2028 assumptions:**
- Revenue of ¥680bn sits between guidance of ¥650bn and the internal note's ¥700bn. The bridge is Cell 8 (Ono) ramping sequentially from FY3/2028 [PRES p.13], plus continued area-driven growth in existing high-end capacity.
- Operating profit of ¥185bn implies a 27.2% group margin, versus guidance of 25.4% and consensus of ~29.2% (¥190bn on ¥650bn). I am above guidance because guidance embeds ~¥10bn of ASP-decline drag that current supply tightness does not support [BROKERS ¶1], and because Cell 8 depreciation (guided at ¥90bn for FY3/2028 [CALL ¶Capex]) is already in my cost base.
- I am below the ¥190bn consensus because a 29%+ group margin would require Electronics to run well above the ~31% management plans, in the first full year of a new plant ramp with qualification and yield risk.

**Where I deliberately do not forecast:** Cell 6 revenue (mass production from FY3/2030, contributions FY3/2031 [TANSHIN p.14; CALL ¶Q1-A1]), Cell 7/Cell 9 (undecided), and EMIB-T (yield still unresolved [CALL ¶Q4-A2]). These are optionality, not forecast items.

---

## 6. Forecast bridges

**Revenue bridge, H1 FY3/2026 → H1 FY3/2027 (actual, ¥bn):**

| Driver | Amount | Basis |
|---|---|---|
| Electronics volume/area and mix | +59.0 | Segment revenue 113.5 → 172.5 [TANSHIN p.12] |
| Ceramics | +6.5 | 39.7 → 46.2 |
| Others | +1.6 | 42.2 → 43.8 |
| Total | +67.1 | 195.5 → 262.5 |

I cannot decompose the Electronics ¥59.0bn into volume, price and mix: the company does not disclose substrate area, ASP or product-level revenue. **This is a genuine data limitation, not a rounding residual.** Management's qualitative statement is that both pricing and AI-related volume contributed [CALL ¶H1], but no split is given.

**Operating-profit bridge, H1 FY3/2026 → H1 FY3/2027 (actual, ¥bn):**

| Driver | Amount | Basis |
|---|---|---|
| Gross profit increase | +29.8 | 67.9 → 97.7 [TANSHIN p.7] |
| SG&A increase | −2.2 | 35.3 → 37.6 |
| Operating profit increase | +27.5 | 32.6 → 60.1 |

Within gross profit, the split between price, mix, utilization and yield is not disclosed. The 41% incremental margin is the observable outcome; attributing it to specific drivers would be speculation.

**FY3/2028 operating-profit bridge (my estimate, ¥bn):**

| Driver | Amount | Basis |
|---|---|---|
| FY3/2027 base operating profit | 137 | My estimate |
| Cell 8 volume and fixed-cost absorption | +30 to +40 | Sequential ramp from FY3/2028 [PRES p.13] |
| Existing-capacity mix and utilization | +15 to +25 | Area demand growth vs 2x capacity plan [PRES p.12] |
| ASP decline | −10 to −15 | Management's embedded assumption; GS estimates ~¥10bn [BROKERS ¶1] |
| Depreciation increase | −9 | ¥81bn → ¥90bn [CALL ¶Capex] |
| Ceramics/Others | −3 to 0 | Automotive weakness, no recovery assumed |
| **FY3/2028 operating profit** | **185** | |

The bridge is deliberately wide. The Cell 8 contribution is the single largest uncertainty and is not independently verifiable from the corpus — no document states Cell 8's capacity, revenue or margin.

**Operating profit to net income, FY3/2028E (¥bn):** operating profit 185 + net non-operating income ~4 (interest income less interest expense, less depreciation of inactive assets) = pretax 189; tax at 34% = −64; minorities −1; **net profit 124–126**. Net/operating profit conversion of ~67%, consistent with the 64.2% achieved in H1 FY3/2027 (38.6/60.1) [TANSHIN p.7].

---

## 7. Consensus and variant perception

**The base case does not currently provide a material forecast edge versus the available consensus.**

| Metric | Guidance | Consensus (Citi ref.) | My base | Difference vs consensus |
|---|---|---|---|---|
| FY3/2027 operating profit | ¥133bn | n/a | ¥137bn | n/a |
| FY3/2028 operating profit | ¥165bn | ~¥190bn | ¥185bn | −¥5bn (−2.6%) |
| FY3/2028 revenue | ¥650bn | n/a | ¥680bn | n/a |

My FY3/2028 operating profit is 2.6% below the single consensus reference available. That is not a differentiated view; it is within the noise of a forecast built on undisclosed capacity and yield assumptions.

**Where an edge could plausibly come from, and why I am not claiming one:**
- *Timing of the Cell 7/Cell 9 decision.* Management targets disclosure within FY3/2027 [CALL ¶Q1-A1; BROKERS ¶1]. This is a genuine near-term catalyst, but it is a capital-allocation announcement, not an earnings revision, and the market is already expecting it (JPM notes the share price fell partly because no Cell 7/Cell 9 investment was announced [BROKERS ¶5]).
- *Pricing.* If the ~¥10bn ASP-decline assumption in guidance proves unnecessary, FY3/2028 operating profit moves toward ¥175bn on guidance revenue. But management has explicitly and repeatedly said it will not assume current peak pricing persists [CALL ¶Q2-A2], and a third AI-GPU supplier is entering [CALL ¶Q2-A1]. I have no evidence to contradict management on this.
- *Cash-flow quality.* Customer advances of ¥199.7bn [TANSHIN p.5] reduce funding and stranded-capacity risk. This is a balance-sheet positive that consensus multiples may not fully credit, but it is not an earnings edge.

**Evidence that would confirm a positive edge:** Q3 FY3/2027 operating profit above ¥37bn with gross margin at or above 38.5%; Electronics margin sustained above 30%; no broad legacy price-down in the January price round.

**Evidence that would invalidate it:** Q3 operating profit below ¥33bn; Electronics margin below 28%; a Cell 8 qualification delay; material price concessions returning.

---

## 8. Cash flow and balance sheet

**Operating cash flow quality is the weakest part of the story.** H1 FY3/2027 operating cash flow of ¥193.2bn [TANSHIN p.9] is roughly 2.6x the underlying run-rate once the ¥118.8bn advance inflow is removed. Customer advances are a real economic positive — they evidence customer commitment and fund capacity — but they are a liability that unwinds as revenue is recognised, and they must not be treated as recurring cash generation.

**Working capital:** receivables rose ¥9.3bn, inventories ¥12.0bn, payables ¥11.2bn [TANSHIN p.9]. Inventory build (¥82.7bn total, up from ¥71.0bn at March 2026 [TANSHIN p.4]) is consistent with ramping production and is not alarming at this stage, but it bears watching if revenue growth slows.

**Capital expenditure:** H1 PP&E purchases of ¥48.3bn [TANSHIN p.10] against a full-year plan of ~¥200–210bn [CALL ¶Capex; BROKERS ¶6] implies H2 capex of roughly ¥150–160bn — a heavily back-loaded year. FY3/2028 capex is planned at ~¥350bn and above ¥400bn annually from FY3/2029 [CALL ¶Capex].

**Free cash flow:** reported H1 free cash flow was ~¥144bn; normalized (excluding advances) it was ~¥25bn. On my FY3/2028 estimates — operating profit ¥185bn, D&A ¥90bn, tax ~¥64bn, capex ¥350bn — normalized free cash flow is approximately **−¥140bn**. The internal note reaches a similar conclusion (−¥132bn) [NOTE §16.2]. This is a capital-intensity and valuation issue, not a liquidity issue.

**Balance sheet at 30 September 2026** [TANSHIN pp.4–6]:

| ¥bn | Sep 2026 | Mar 2026 |
|---|---|---|
| Cash and deposits | 420.5 | 295.7 |
| Bonds (current + non-current) | 45.0 | 60.0 |
| Convertible bonds | 56.0 | 72.5 |
| Long-term loans | 61.0 | 60.0 |
| Lease obligations | 0.05 | 0.06 |
| **Total interest-bearing debt** | **162.1** | **192.5** |
| **Net cash** | **258.4** | **103.2** |
| Equity ratio | 53.5% | 57.3% |

Net cash more than doubled, but almost entirely because of customer advances. After the October convertible conversion, debt falls by ¥11.5bn and shares rise by 5.1m [TANSHIN p.13].

**Capital allocation:** progressive dividend to FY3/2031 with a ~20% payout guideline, alongside growth investment, R&D (¥37bn, 6–7% of sales in FY3/2027 [CALL ¶R&D]) and debt reduction [PRES p.17]. Buybacks are negligible (¥9m of purchases in H1 [TANSHIN p.10]). Shareholder return is therefore almost entirely dependent on earnings growth and the multiple; the dividend yield of 0.25% provides no support.

**Commitments:** the ¥151bn Cell 6 investment is incremental to the ¥220bn previously announced for Cell 6 and the ¥280bn for Cell 8 [PRES p.13]. Management states the Cell 6 investment is customer-backed and that revenue at maturity should be roughly one times the investment amount at a 30%+ operating margin [CALL ¶Q3-A1; BROKERS ¶5]. That is a company expectation, not a contracted commitment.

---

## 9. Valuation

**Method selection.** IBIDEN is a capital-intensive manufacturer in a structural growth phase with negative normalized free cash flow. P/E alone is insufficient because earnings are depressed relative to the capital being deployed; EV/EBITDA is the better cross-check because it neutralises the depreciation wave. I use both, and I do not use a DCF because the corpus does not provide the capacity, yield and pricing detail needed to build a defensible long-run cash-flow profile.

**Share basis.** 570.7m net shares after the October conversion; ~590m fully diluted assuming full conversion of the remaining ¥44.5bn of convertible bonds at ¥2,243.1 [TANSHIN pp.5, 13, 14]. Market capitalisation at ¥8,045 is ¥4,591bn on net shares.

**Enterprise value.** Net cash of ¥258bn at September 2026, falling to roughly ¥90bn by March 2028 on my free-cash-flow estimates. EV is therefore ~¥4,333bn currently and ~¥4,500bn on FY3/2028 estimates.

| Metric | FY3/2027E | FY3/2028E |
|---|---|---|
| Diluted EPS | ~¥157 | ~¥212 |
| P/E at ¥8,045 | 51.2x | 38.0x |
| EBITDA | ~¥218bn | ~¥275bn |
| EV/EBITDA | ~19.9x | ~16.4x |
| Normalized FCF | ~−¥30bn | ~−¥140bn |
| Dividend yield | 0.25% | 0.25% |

**Fair-value range: ¥6,800–8,000.**

- *P/E anchor:* 30–36x FY3/2028 diluted EPS of ~¥212 gives ¥6,400–7,600. A 30–36x band is appropriate for a business with 20%+ structural growth but negative free cash flow, meaningful dilution and a first-year plant ramp; it is below the ~38x the market currently pays.
- *EV/EBITDA anchor:* 14–17x FY3/2028 EBITDA of ~¥275bn gives an EV of ¥3,850–4,675bn; adding ~¥90bn of net cash and dividing by 590m diluted shares gives ¥6,700–8,100.
- The two methods overlap at roughly ¥6,700–7,600; I widen the top of the range to ¥8,000 to reflect the unmodeled optionality from Cell 6, Cell 7 and Cell 9, which management values at a 30%+ operating margin on roughly one turn of revenue [CALL ¶Q3-A1].

**Sensitivities:** each ¥10bn of FY3/2028 operating profit is worth roughly ¥11 of diluted EPS, or ~¥400 per share at a 35x multiple. A 5ppt change in the applied multiple moves fair value by ~¥1,000 per share. A one-year delay to Cell 8 would remove both revenue and depreciation absorption simultaneously and is the single largest downside driver.

**Why this framework is appropriate:** it prices the earnings that are actually visible within the forecast period and treats the post-FY3/2028 capacity pipeline as optionality rather than as a valuation input. It does not reverse-engineer a multiple to reach a target.

**Vendor snapshot conflict:** Bloomberg shows a P/E of 56.2x at ¥8,045 [vendor fundamentals]. That implies EPS of ~¥143, which reconciles with neither FY3/2026 reported EPS (~¥113) nor FY3/2027 guidance EPS (¥157.17). I treat it as an unreconciled vendor snapshot and use my own computed multiples.

---

## 10. Catalysts within the decision horizon

1. **Q3 FY3/2027 results (late January / early February 2027).** The key test of whether the H2 guidance step-up is being delivered. Watch Q3 operating profit against ~¥36–37bn and gross margin against 38.5%.
2. **Cell 7 / Cell 9 investment decision and disclosure (management target: within FY3/2027, i.e. by March 2027)** [CALL ¶Q1-A1; BROKERS ¶1]. Management indicates scale similar to Cell 6 and Cell 8, with customer terms to be finalised. A customer-backed announcement with disclosed returns would be a positive; another deferral would reinforce the "conservative, half-hearted plan" reading that JPM and SMBC both flagged [BROKERS ¶5, ¶6].
3. **Possible revision of the FY3/2031 ¥300bn operating-profit target.** Management has said it will revisit the target once capex decisions are finalised, potentially at Q3 or full-year results [BROKERS ¶1; CALL ¶Q4-A1]. The current target excludes the ¥151bn Cell 6 investment, which management describes as "pure upside" [CALL ¶Q1-A1].
4. **FY3/2027 full-year results and FY3/2028 guidance (late April 2027)** — at the outer edge of the horizon. The gap between guidance of ¥165bn and consensus of ~¥190bn is the number that matters.
5. **Convertible bond conversion progress** — ongoing dilution, with ¥44.5bn face remaining [TANSHIN p.5].
6. **Interim dividend of ¥10.0 per share payable 25 November 2026** [TANSHIN pp.1–2] — immaterial to return at a 0.25% yield.

---

## 11. Risks and invalidation conditions

**Business risk.** A third AI-GPU substrate supplier is entering, shifting the market from two players to three [CALL ¶Q2-A1]. Management argues total industry capacity will still fall far short of demand, and that its own share is not at risk. That is a management assertion, not evidence. Citi separately notes that Ibiden's high-end domestic capacity grows ~2x against 3.5x market area demand, which mathematically implies share loss [BROKERS ¶3]. The two statements are not reconciled in the corpus.

**Forecast risk.** FY3/2028 growth depends on Cell 8 ramping sequentially [PRES p.13]. No document in the corpus quantifies Cell 8's capacity, revenue or margin. A qualification delay would hit revenue and depreciation absorption at the same time.

**Pricing risk.** Guidance embeds ASP declines that current conditions do not support [BROKERS ¶1]. If those declines materialise earlier or more deeply than assumed, my FY3/2028 estimate falls toward guidance of ¥165bn.

**Technology risk.** EMIB-T yield remains unresolved. Management states it is "struggling significantly with yield improvement" and that the bottleneck is forming interconnections on the underside of the embedded bridge chip [CALL ¶Q4-A2]. Mass production is scheduled for H2 FY3/2028 (company "H2 next year") and management says the schedule is unchanged, but this is a schedule assertion, not demonstrated yield.

**Balance-sheet and cash-flow risk.** Capex of ~¥350bn in FY3/2028 against operating cash flow of ~¥215bn produces negative free cash flow of ~¥140bn. Net cash of ¥258bn would fall to roughly ¥90bn by March 2028. Further capacity commitments (Cell 7, Cell 9, Cell 8 spare space) could extend the negative free-cash-flow period. This is a capital-efficiency risk, not a solvency risk, given the equity ratio of 53.5%.

**Dilution risk.** Full conversion of the remaining convertible bonds adds ~19.9m shares, ~3.4% of the current base, reducing EPS participation in earnings growth.

**Valuation risk.** At 51x FY3/2027 guidance EPS and ~38x my FY3/2028 estimate, with negative normalized free cash flow, there is limited protection against an execution delay. The share price already fell sharply on the results and briefing [BROKERS ¶5], which suggests the market is repricing the medium-term plan rather than the near-term numbers.

**Regulatory / geopolitical risk.** Not addressed in the corpus. The business is exposed to US–China semiconductor policy and to customer concentration in US AI accelerator programmes, but no document quantifies this.

**Thesis invalidation conditions (observable):**
- Q3 FY3/2027 operating profit below ¥33bn or gross margin below 38%.
- Electronics segment margin below 28% in any quarter.
- A Cell 8 qualification delay pushing meaningful revenue beyond FY3/2028.
- A Cell 7/Cell 9 decision deferred beyond FY3/2027, or announced without customer backing.
- Broad price concessions returning in the January 2027 price round.
- Capex rising without matching customer commitments or advances.

---

## 12. Scenario analysis

I do not assign probabilities; no documented methodology supports them.

**Bear case — fair value ~¥5,500–6,500.**
FY3/2028 revenue ¥620bn, operating profit ¥150bn (below guidance), as ASP declines arrive early, Cell 8 ramps slowly, and a third supplier takes allocation. Depreciation of ¥90bn absorbs the margin. Net profit ~¥98bn, diluted EPS ~¥166. At 33–39x, fair value ¥5,500–6,500. *Confirming evidence:* Q3 operating profit below ¥33bn; Electronics margin below 28%; price-down in January. *Invalidating evidence:* sustained 30%+ Electronics margins through H2 FY3/2027.

**Base case — fair value ~¥7,000–8,000.**
FY3/2028 revenue ¥680bn, operating profit ¥185bn, net profit ¥125bn, diluted EPS ~¥212. Cell 8 ramps through the year; existing capacity stays fully utilised; ASP declines are modest. At 33–38x, fair value ¥7,000–8,000. *Confirming evidence:* Q3 operating profit ~¥36–37bn; gross margin ≥38.5%; Cell 8 installation on schedule. *Invalidating evidence:* any of the bear-case triggers.

**Bull case — fair value ~¥9,000–10,000.**
FY3/2028 revenue ¥720bn, operating profit ¥210bn, as pricing holds, Cell 8 ramps faster, and mix shifts further toward AI accelerators. Net profit ~¥142bn, diluted EPS ~¥241. At 37–41x, fair value ¥9,000–10,000. *Confirming evidence:* Electronics margin above 33%; a customer-backed Cell 7/Cell 9 announcement with disclosed returns; an upward revision of the FY3/2031 target. *Invalidating evidence:* a third supplier qualifying at scale with acceptable yield.

The scenarios differ on operations, not merely on the multiple. The multiple range is held broadly constant at 33–41x across cases, with the variation reflecting the confidence with which the earnings are expected to persist beyond FY3/2028.

---

## 13. Key monitoring indicators

| Indicator | Base-case confirmation | Warning signal |
|---|---|---|
| Q3 FY3/2027 operating profit | ≥¥36.5bn | <¥33bn |
| Q3 gross margin | ≥38.5% | <38% |
| Electronics segment margin | ≥30% | <28% |
| Ceramics H2 operating profit | ~¥2.1bn as guided | Below ¥1bn |
| Cell 8 installation / qualification | On schedule for FY3/2028 ramp | Any qualification delay |
| Cell 7 / Cell 9 decision | Announced within FY3/2027 with customer terms | Deferred past March 2027 |
| Customer advances | Stable or rising | Decline, or terms weakening |
| Capex vs plan | ~¥200–210bn FY3/2027 | Material overrun without commitments |
| Depreciation FY3/2028 | ~¥90bn | Materially above plan |
| Convertible bond conversion | Gradual | Accelerated dilution |
| ASP / price round (January 2027) | No broad price-down | Material concessions |

---

## 14. Summary table

| Item | Assessment |
|---|---|
| Instrument | 4062.T (IBIDEN CO., LTD., Tokyo/Nagoya) |
| Analysis date | 2026-11-02 |
| Decision horizon | 3–6 months |
| Fundamental rating | **Neutral** |
| Revenue direction | Positive; H1 FY3/2027 +34.3%, FY3/2027 guidance +35.8% |
| Earnings direction | Positive but decelerating; H1 operating profit +84.6%, FY3/2028 guidance +24% |
| Forecast versus guidance | Modestly above: FY3/2027 ¥137bn vs ¥133bn; FY3/2028 ¥185bn vs ¥165bn |
| Forecast versus consensus | In line: ¥185bn vs ~¥190bn referenced by Citi — no material edge |
| Cash-flow quality | Weak normalized: H1 operating cash flow of ¥193.2bn includes ¥118.8bn of customer advances |
| Balance-sheet condition | Strong: net cash ~¥258bn, equity ratio 53.5%, but net cash falls to ~¥90bn by FY3/2028 on my estimates |
| Valuation | Demanding: ~51x FY3/2027 guidance EPS, ~38x FY3/2028E diluted EPS, ~16x FY3/2028E EV/EBITDA, negative normalized FCF. Fair value ¥6,800–8,000 |
| Main catalyst | Cell 7 / Cell 9 investment decision and possible FY3/2031 target revision, both targeted within FY3/2027 |
| Main risk | ASP erosion and share dilution as a third AI-GPU substrate supplier qualifies, combined with Cell 8 ramp execution |
| Positive confirmation | Q3 FY3/2027 operating profit ≥¥36.5bn with gross margin ≥38.5%; Electronics margin ≥30% |
| Thesis invalidation | Q3 operating profit <¥33bn; Electronics margin <28%; Cell 8 qualification delay; Cell 7/9 decision deferred past March 2027 |
| Principal data limitation | No company-disclosed consensus, no peer valuation data, no disclosed substrate area/ASP/customer concentration, and the two internal notes carry unverified publication timing and internally inconsistent figures |

---

### Documents cited

| Label | Document ID |
|---|---|
| TANSHIN | doc-4062.T-ibiden-co-ltd-semi-annual-tanshin (filing, published 2026-10-29) |
| PRES | doc-4062.T-ibiden-co-ltd-company-presentation-20261030 (presentation, published 2026-10-30) |
| BROKERS | doc-4062.T-brokers-views-oct-30-2026 (internal note, published 2026-10-30) |
| NOTE | doc-4062.T-ibiden-q2-fy2027-results-forecast (internal note, publication date unverified) |
| CALL | doc-4062.T-ibiden-q2-fy3-2027-result-call (internal note, publication date unverified) |

**Source classification:** TANSHIN and PRES are public company documents. BROKERS, NOTE and CALL are **private internal research material** — they are not company disclosure and are used here only as evidence of broker views and of management commentary as recorded by a third party. Where an internal note conflicts with the filing, the filing prevails. Vendor data (Bloomberg fundamentals, income statement, balance sheet, cash flow) is used only for figures the documents do not state, and is labelled as such.