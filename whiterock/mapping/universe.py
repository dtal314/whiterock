"""Sector universe plus transparent action-to-sector mapping rules.

Every mapping decision is rule-based and auditable: an action is linked to a
sector by (a) the issuing agency and (b) keyword hits in its title, abstract,
and action line. Direction (benefit vs. suffer) comes from sector-specific
patterns first, then a generic lexicon. The matched terms are returned so the
dashboard can show WHY a link was made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    tickers: tuple[str, ...]
    benchmark: str
    agencies: tuple[str, ...]        # substrings matched against FR agency names
    keywords: tuple[str, ...]        # regex fragments, case-insensitive
    committees: tuple[str, ...]      # thomas_id prefixes from congress-legislators
    # (pattern, direction) where direction is +1 (benefit) or -1 (suffer)
    direction_rules: tuple[tuple[str, int], ...] = field(default_factory=tuple)


MARKET_BENCHMARK = "SPY"

SECTORS: tuple[Sector, ...] = (
    Sector(
        "defense", "Defense contractors",
        ("LMT", "RTX", "NOC", "GD", "LHX", "HII", "BA"), "ITA",
        ("Defense Department", "Army Department", "Navy Department", "Air Force Department",
         "Defense Acquisition Regulations"),
        (r"\bdefense\b", r"\bmissile", r"\bmunition", r"\bwarfighter", r"\bmilitary\b",
         r"\bnational security\b", r"\bshipbuilding", r"\bgolden dome\b", r"\bweapon"),
        ("HSAS", "SSAS", "HSAP", "SSAP"),
        ((r"increase|expand|acceler|procure|award|reform.*acquisition|shipbuild|build", +1),
         (r"cut|reduc|cancel|terminat", -1)),
    ),
    Sector(
        "oil_gas", "Oil and gas",
        ("XOM", "CVX", "COP", "OXY", "EOG", "SLB", "KMI"), "XLE",
        ("Energy Department", "Interior Department", "Land Management Bureau",
         "Ocean Energy Management", "Federal Energy Regulatory Commission",
         "Pipeline and Hazardous Materials", "Safety and Environmental Enforcement"),
        (r"\boil\b", r"\bnatural gas\b", r"\bdrilling", r"\bpipeline", r"\bLNG\b",
         r"liquefied natural gas", r"offshore leas", r"\bpetroleum", r"\brefiner",
         r"\bcrude\b", r"oil and gas leas", r"energy dominance", r"energy emergency"),
        ("HSII", "SSEG", "HSIF"),
        ((r"leas(e|ing)|permit|approv|expand|unleash|dominance|emergency|export|streamlin|rescind.*(rule|restriction)", +1),
         (r"methane fee|prohibit|withdraw.*leas|moratorium|ban\b|royalty increase|penalt", -1)),
    ),
    Sector(
        "clean_energy", "Clean energy",
        ("FSLR", "ENPH", "NEE", "RUN", "ARRY", "BE"), "ICLN",
        ("Energy Department", "Environmental Protection Agency", "Internal Revenue Service",
         "Treasury Department"),
        (r"\bsolar\b", r"\bwind (energy|power|farm|turbine)", r"\brenewable", r"clean energy",
         r"\b45[XYZ]\b|\b48E\b|\b45V\b", r"energy (tax )?credit", r"\bbattery storage",
         r"\bhydrogen\b", r"\bgeothermal"),
        ("HSIF", "SSEG", "HSWM", "SSFI"),
        ((r"credit|grant|loan|incentiv|award|expand|extend", +1),
         (r"terminat|repeal|phase.?out|rescind|restrict|foreign entity of concern|cancel|eliminat", -1)),
    ),
    Sector(
        "utilities", "Electric utilities",
        ("NEE", "DUK", "SO", "AEP", "VST", "CEG"), "XLU",
        ("Federal Energy Regulatory Commission", "Energy Department", "Nuclear Regulatory Commission"),
        (r"\btransmission\b", r"electric grid", r"\bgrid reliab", r"\butility\b", r"\butilities\b",
         r"\bratepayer", r"\bnuclear (power|reactor|energy)", r"\bdata center (power|load|electric)"),
        ("HSIF", "SSEG"),
        ((r"reliab|expedit|approv|nuclear|streamlin|incentiv", +1), (r"penalt|prohibit|cap\b|refund", -1)),
    ),
    Sector(
        "pharma", "Pharma and biotech",
        ("PFE", "MRK", "LLY", "JNJ", "ABBV", "AMGN", "BMY", "GILD"), "XLV",
        ("Food and Drug Administration", "Health and Human Services Department",
         "Centers for Medicare & Medicaid Services", "National Institutes of Health"),
        (r"\bdrug pric", r"\bprescription drug", r"most.?favored.?nation", r"\bbiologic",
         r"\bvaccine", r"\bpharmaceutical", r"drug (approval|shortage|import)", r"\bmedicare part d",
         r"\bnegotiation program", r"\bFDA\b"),
        ("HSIF", "SSHR", "SSFI", "HSWM"),
        ((r"approv|expedit|priority review|exclusiv|fast.?track|tariff exemption", +1),
         (r"pric(e|ing) (cap|control|negotiat)|most.?favored|negotiat|import|penalt|recall|revok|tariff", -1)),
    ),
    Sector(
        "health_insurers", "Health insurers and providers",
        ("UNH", "CVS", "ELV", "CI", "HUM", "HCA"), "XLV",
        ("Centers for Medicare & Medicaid Services", "Health and Human Services Department"),
        (r"medicare advantage", r"\bmedicaid\b", r"\bmedicare\b", r"affordable care act", r"\bACA\b",
         r"health insurance", r"\bmarketplace (plan|coverage)", r"\bhospital (payment|outpatient|inpatient)",
         r"prior authorization", r"risk adjustment"),
        ("HSIF", "HSWM", "SSFI", "SSHR"),
        ((r"rate (increase|notice)|payment increase|expand|extend|flexib", +1),
         (r"cut|reduc|clawback|audit|penalt|work requirement|eligibility restrict|rescind", -1)),
    ),
    Sector(
        "banks", "Banks and brokers",
        ("JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW"), "XLF",
        ("Federal Reserve System", "Comptroller of the Currency", "Federal Deposit Insurance Corporation",
         "Consumer Financial Protection Bureau", "Securities and Exchange Commission", "Treasury Department"),
        (r"\bbank(s|ing)?\b", r"\bcapital requirement", r"\bBasel\b", r"\bleverage ratio",
         r"\bdeposit insurance", r"\bcredit card", r"\bmortgage (lending|servicing|rule)", r"\bswap dealer",
         r"\bbroker.?dealer", r"\bstress test", r"\boverdraft", r"\bcommunity reinvestment"),
        ("HSBA", "SSBK"),
        ((r"reduc.*(capital|burden)|tailor|deregulat|rescind|withdraw|delay|exempt|simplif|relief", +1),
         (r"cap\b|prohibit|penalt|enforcement|fee limit|increase.*capital|restrict", -1)),
    ),
    Sector(
        "crypto", "Crypto and fintech",
        ("COIN", "MSTR", "HOOD", "XYZ", "PYPL", "MARA"), "XLF",
        ("Securities and Exchange Commission", "Treasury Department", "Commodity Futures Trading Commission",
         "Financial Crimes Enforcement Network"),
        (r"\bcrypto", r"digital asset", r"\bstablecoin", r"\bbitcoin", r"\bblockchain",
         r"\bvirtual currenc", r"\bdecentralized finance", r"strategic bitcoin reserve"),
        ("HSBA", "SSBK", "HSAG", "SSAF"),
        ((r"framework|clarity|reserve|approv|safe harbor|exempt|rescind|withdraw", +1),
         (r"enforcement|prohibit|penalt|restrict|ban\b|fraud", -1)),
    ),
    Sector(
        "big_tech", "Large technology platforms",
        ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "ORCL"), "XLK",
        ("Federal Trade Commission", "Justice Department", "Antitrust Division", "Commerce Department",
         "National Telecommunications and Information Administration", "Federal Communications Commission",
         "Office of Science and Technology Policy"),
        (r"\bantitrust", r"\bartificial intelligence\b", r"\bAI (model|system|infrastructure|action plan)",
         r"\bdata (privacy|broker)", r"\bplatform", r"\bapp store", r"\bsocial media", r"\bcloud comput",
         r"\bdata center", r"section 230", r"\bcontent moderation", r"\bchildren.?s online"),
        ("HSJU", "SSJU", "HSIF", "SSCM"),
        ((r"acceler|promot|streamlin|permit|infrastructure|invest|deregulat|preempt", +1),
         (r"antitrust|break.?up|divestiture|prohibit|penalt|fine|investigat|restrict|ban\b|liabil", -1)),
    ),
    Sector(
        "semiconductors", "Semiconductors",
        ("NVDA", "AMD", "INTC", "AVGO", "MU", "QCOM", "TSM"), "SOXX",
        ("Industry and Security Bureau", "Commerce Department"),
        (r"\bsemiconductor", r"\bchips?\b", r"\bexport control", r"advanced computing",
         r"\bintegrated circuit", r"\bfoundry", r"\bCHIPS (Act|and Science)", r"entity list",
         r"\bAI (chip|accelerator|diffusion)"),
        ("HSIF", "SSCM", "HSFA", "SSFR"),
        ((r"incentiv|grant|award|tax credit|rescind.*(rule|control)|license.*(approv|grant)|invest", +1),
         (r"export control|entity list|restrict|prohibit|license requirement|tariff|section 232|penalt", -1)),
    ),
    Sector(
        "telecom", "Telecom and media",
        ("T", "VZ", "TMUS", "CMCSA", "CHTR", "DIS"), "XLC",
        ("Federal Communications Commission", "National Telecommunications and Information Administration"),
        (r"\bspectrum\b", r"\bbroadband", r"\btelecommunications", r"\bnet neutrality", r"\buniversal service",
         r"\b5G\b", r"\bwireless\b", r"\bbroadcast", r"\bcable (operator|television)"),
        ("HSIF", "SSCM"),
        ((r"auction|allocat|deregulat|streamlin|approv|grant|fund", +1),
         (r"prohibit|penalt|forfeiture|cap\b|rate regulat|restrict", -1)),
    ),
    Sector(
        "industrials", "Construction and infrastructure",
        ("CAT", "DE", "VMC", "MLM", "URI", "ETN", "JCI"), "XLI",
        ("Transportation Department", "Federal Highway Administration", "Federal Transit Administration",
         "Engineers Corps", "Federal Railroad Administration"),
        (r"\binfrastructure", r"\bhighway", r"\bbridge", r"\bconstruction", r"\btransit\b", r"\bfreight rail",
         r"\bwater infrastructure", r"\bports?\b", r"\bpermitting reform", r"\bNEPA\b"),
        ("HSPW", "SSEV", "HSAP", "SSAP"),
        ((r"fund|grant|award|permit|streamlin|expedit|invest|reform", +1), (r"rescind|cancel|freez|penalt", -1)),
    ),
    Sector(
        "steel_materials", "Steel, aluminum and mining",
        ("NUE", "STLD", "CMC", "CLF", "FCX", "AA", "MP"), "XLB",
        ("International Trade Administration", "Commerce Department", "U.S. Customs and Border Protection",
         "Trade Representative", "International Trade Commission"),
        (r"\btariff", r"\bsteel\b", r"\baluminum", r"section 232", r"\bantidumping", r"\bcountervailing",
         r"\bcopper\b", r"\bcritical mineral", r"\brare earth", r"\bmining\b"),
        ("HSWM", "SSFI", "HSII", "SSEG"),
        ((r"tariff|duty|duties|section 232|antidumping|countervailing|critical mineral|domestic|buy american|mining|permit", +1),
         (r"exemption|exclusion|suspend.*tariff|terminat.*(tariff|duty)|rescind", -1)),
    ),
    Sector(
        "agriculture", "Agriculture and food",
        ("ADM", "BG", "CTVA", "MOS", "TSN", "CF"), "MOO",
        ("Agriculture Department", "Commodity Credit Corporation", "Farm Service Agency", "Food and Drug Administration"),
        (r"\bfarm(er)?s?\b", r"\bcrop", r"\bethanol", r"\bbiofuel", r"\bfertilizer", r"\bSNAP\b",
         r"\bcommodit", r"\bsoybean", r"\bcorn\b", r"\blivestock", r"renewable fuel standard", r"\bE15\b"),
        ("HSAG", "SSAF"),
        ((r"assistance|payment|support|fund|blend|E15|waiver|export|purchase", +1),
         (r"cut|reduc|prohibit|penalt|restrict|recall|ban\b|dye", -1)),
    ),
    Sector(
        "transport", "Airlines, rail and logistics",
        ("DAL", "UAL", "AAL", "LUV", "FDX", "UPS", "UNP", "CSX"), "IYT",
        ("Federal Aviation Administration", "Transportation Department", "Surface Transportation Board",
         "Federal Motor Carrier Safety Administration"),
        (r"\bairline", r"\baviation", r"\bair traffic", r"\bFAA\b", r"\bfreight rail", r"\btrucking",
         r"\bhours of service", r"\bde minimis", r"\bpostal", r"\bairport"),
        ("HSPW", "SSCM"),
        ((r"modern|fund|invest|deregulat|streamlin|exempt|delay", +1),
         (r"refund|penalt|fee|prohibit|restrict|cap\b|de minimis", -1)),
    ),
    Sector(
        "housing", "Housing and homebuilders",
        ("DHI", "LEN", "PHM", "NVR", "TOL", "HD"), "XHB",
        ("Housing and Urban Development Department", "Federal Housing Finance Agency",
         "Federal Housing Administration"),
        (r"\bhousing\b", r"\bhomebuild", r"\bmortgage", r"\bFHA\b", r"\bFannie Mae", r"\bFreddie Mac",
         r"\bhome ?buyer", r"\bzoning", r"\bmanufactured hous"),
        ("HSBA", "SSBK", "HSPW"),
        ((r"expand|lower.*(cost|fee|premium)|incentiv|streamlin|deregulat|supply|open.*land", +1),
         (r"cut|penalt|restrict|increase.*(fee|premium)|tariff.*lumber", -1)),
    ),
    Sector(
        "immigration_enforcement", "Immigration enforcement and security contractors",
        ("GEO", "CXW", "AXON", "PLTR", "LDOS"), "XLI",
        ("Homeland Security Department", "U.S. Immigration and Customs Enforcement",
         "U.S. Customs and Border Protection", "U.S. Citizenship and Immigration Services"),
        (r"\bimmigra", r"\bdetention", r"\bborder\b", r"\bdeportation", r"\bremoval", r"\bICE\b",
         r"\balien", r"\basylum", r"\bvisa\b"),
        ("HSHM", "SSHS", "HSJU", "SSJU"),
        ((r"enforce|detention|expand|surge|contract|remov|secur", +1), (r"limit|restrict|close|reduc", -1)),
    ),
    Sector(
        "space", "Space and launch",
        ("RKLB", "LUNR", "ASTS", "BA", "LMT", "RDW"), "ITA",
        ("National Aeronautics and Space Administration", "Space Force", "Federal Communications Commission",
         "Commercial Space Transportation"),
        (r"\bspace (launch|force|policy|explor|academy|industr)", r"\bsatellite", r"\bNASA\b", r"\borbit",
         r"\blaunch (vehicle|license|site)", r"\bArtemis", r"\bcommercial space"),
        ("HSSY", "SSCM", "HSAS", "SSAS"),
        ((r"acceler|streamlin|award|fund|approv|commercial|expand", +1), (r"cancel|terminat|cut|reduc", -1)),
    ),
    Sector(
        "retail", "Retail and consumer goods",
        ("WMT", "TGT", "COST", "NKE", "BBY", "DG"), "XRT",
        ("U.S. Customs and Border Protection", "Trade Representative", "Consumer Product Safety Commission",
         "Federal Trade Commission"),
        (r"\btariff", r"\bconsumer (goods|products)", r"\bimport dut", r"\bimports? of\b", r"\bde minimis",
         r"\breciprocal", r"\bretail", r"\bapparel", r"\bfootwear", r"\btoys?\b", r"\bjunk fee"),
        ("HSWM", "SSFI", "HSIF", "SSCM"),
        ((r"exemption|exclusion|suspend|pause|reduc.*tariff|extend.*(pause|exemption)", +1),
         (r"tariff|duty|duties|reciprocal|de minimis|penalt|prohibit|recall|fee rule", -1)),
    ),
    Sector(
        "autos", "Automakers",
        ("TSLA", "GM", "F", "RIVN", "STLA", "TM"), "XLY",
        ("National Highway Traffic Safety Administration", "Environmental Protection Agency",
         "Transportation Department", "Internal Revenue Service"),
        (r"\bvehicle emission", r"\bfuel economy", r"\bCAFE\b", r"\belectric vehicle", r"\bEV\b",
         r"\bautonomous vehicle", r"\bself.?driving", r"\bautomobile", r"\bmotor vehicle", r"\bauto (parts|tariff|industry)",
         r"\bclean vehicle credit"),
        ("HSIF", "SSCM", "HSWM", "SSFI"),
        ((r"exempt|relax|roll.?back|rescind|approv|framework|streamlin|credit", +1),
         (r"tariff|standard|mandate|recall|penalt|terminat.*credit|prohibit", -1)),
    ),
    Sector(
        "gov_contractors", "Government IT and services contractors",
        ("LDOS", "SAIC", "BAH", "CACI", "PLTR", "ACN"), "XLI",
        ("General Services Administration", "Office of Management and Budget", "Office of Personnel Management",
         "Defense Department", "Department of Government Efficiency"),
        (r"\bfederal contract", r"\bIT modernization", r"\bprocurement", r"\bgovernment efficiency",
         r"\bDOGE\b", r"\bconsulting contract", r"\bfederal workforce", r"\bacquisition (reform|regulation)",
         r"\bcybersecurity", r"\bcloud (services|migration)"),
        ("HSGO", "SSGA", "HSAS", "SSAS"),
        ((r"modern|acceler|award|streamlin|expand|cyber|AI|consolidat.*(procurement|purchasing)", +1),
         (r"terminat|cancel|cut|reduc|review.*contract|consulting|freez|efficiency", -1)),
    ),
)

SECTOR_BY_ID = {s.id: s for s in SECTORS}

ALL_TICKERS: tuple[str, ...] = tuple(sorted({t for s in SECTORS for t in s.tickers}))
ALL_BENCHMARKS: tuple[str, ...] = tuple(sorted({s.benchmark for s in SECTORS} | {MARKET_BENCHMARK}))

# Generic direction lexicon used when no sector-specific rule fires.
GENERIC_BENEFIT = re.compile(
    r"\b(award|grant|fund(ing|s)?|subsid|incentiv|tax credit|approv|authoriz|waiver|exempt|"
    r"deregulat|streamlin|expedit|acceler|expand|support|invest|promot|unleash|relief)",
    re.IGNORECASE,
)
GENERIC_SUFFER = re.compile(
    r"\b(prohibit|ban(ned|s)?\b|restrict|penalt|fine[sd]?\b|cap\b|price control|recall|revok|"
    r"investigat|antitrust|rescind|terminat|cancel|sanction|moratorium|liabilit|enforcement action)",
    re.IGNORECASE,
)


@dataclass
class SectorLink:
    sector_id: str
    relevance: float          # 0..1
    direction: int            # +1 benefit, -1 suffer, 0 unclear
    direction_score: float    # -1..1 continuous
    matched_agencies: list[str]
    matched_keywords: list[str]
    matched_direction: list[str]


_KEYWORD_RE = {s.id: [re.compile(k, re.IGNORECASE) for k in s.keywords] for s in SECTORS}
_DIRECTION_RE = {
    s.id: [(re.compile(p, re.IGNORECASE), d) for p, d in s.direction_rules] for s in SECTORS
}


def map_action(title: str, abstract: str | None, action: str | None, agency_names: list[str],
               min_relevance: float = 0.25) -> list[SectorLink]:
    """Link one government action to sectors. Pure function; no I/O."""
    text = " ".join(x for x in (title, abstract, action) if x)
    links: list[SectorLink] = []
    for sector in SECTORS:
        matched_agencies = [a for a in agency_names if any(sub.lower() in a.lower() for sub in sector.agencies)]
        matched_keywords: list[str] = []
        for rx in _KEYWORD_RE[sector.id]:
            m = rx.search(text)
            if m:
                matched_keywords.append(m.group(0).strip())
        # Relevance: first keyword 0.25, each further distinct keyword 0.2, agency match 0.3,
        # capped at 1.0. Agency alone is never enough (Treasury issues thousands of
        # unrelated documents); a single keyword alone stays below the pipeline threshold.
        n_kw = len(set(matched_keywords))
        relevance = min(1.0, (0.25 + 0.2 * (n_kw - 1) if n_kw else 0.0) + 0.3 * bool(matched_agencies))
        if not matched_keywords:
            relevance = 0.0
        if relevance < min_relevance:
            continue
        # Direction: sector rules first.
        score = 0.0
        matched_dir: list[str] = []
        for rx, d in _DIRECTION_RE[sector.id]:
            m = rx.search(text)
            if m:
                score += d
                matched_dir.append(("+" if d > 0 else "-") + m.group(0).strip())
        if score == 0.0:
            b = len(GENERIC_BENEFIT.findall(text))
            s_ = len(GENERIC_SUFFER.findall(text))
            score = 0.5 * (b - s_) / max(1, b + s_) if (b or s_) else 0.0
            if b:
                matched_dir.append(f"+generic x{b}")
            if s_:
                matched_dir.append(f"-generic x{s_}")
        direction_score = max(-1.0, min(1.0, score / 2.0)) if abs(score) >= 1 else max(-1.0, min(1.0, score))
        direction = 1 if direction_score > 0.15 else (-1 if direction_score < -0.15 else 0)
        links.append(SectorLink(sector.id, round(relevance, 3), direction, round(direction_score, 3),
                                matched_agencies, sorted(set(matched_keywords)), matched_dir))
    links.sort(key=lambda l: -l.relevance)
    return links


def committee_sectors(committee_ids: list[str]) -> set[str]:
    """Sectors a politician's committee seats make relevant."""
    out: set[str] = set()
    for cid in committee_ids:
        for s in SECTORS:
            if any(cid.startswith(prefix) for prefix in s.committees):
                out.add(s.id)
    return out


def ticker_sectors(ticker: str) -> list[str]:
    return [s.id for s in SECTORS if ticker in s.tickers]
