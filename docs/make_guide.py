"""Build the FightIQ weekly guide PDF:  python3 docs/make_guide.py [output.pdf]"""
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/FightIQ_Weekly_Guide.pdf"
NAVY = colors.HexColor("#1F3864")
ACCENT = colors.HexColor("#C00000")
CODE_BG = colors.HexColor("#F2F2F2")
NOTE_BG = colors.HexColor("#FFF7D6")
GRID = colors.HexColor("#BFBFBF")
MUTED = colors.HexColor("#595959")

base = ParagraphStyle("base", fontName="Helvetica", fontSize=10, leading=14, alignment=TA_LEFT)
title = ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY)
subtitle = ParagraphStyle("subtitle", parent=base, fontSize=11, leading=15, textColor=MUTED)
h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY,
                    spaceBefore=12, spaceAfter=6)
h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=15, spaceBefore=6, spaceAfter=3)
small = ParagraphStyle("small", parent=base, fontSize=9, leading=12, textColor=MUTED)
cell = ParagraphStyle("cell", parent=base, fontSize=9, leading=12)
cell_b = ParagraphStyle("cell_b", parent=cell, fontName="Helvetica-Bold")
code = ParagraphStyle("code", parent=base, fontName="Courier-Bold", fontSize=10, leading=13)

W = A4[0] - 36 * mm


def P(text, style=base):
    return Paragraph(text, style)


def cmd(*lines):
    t = Table([[P(l, code)] for l in lines], colWidths=[W - 8 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("BOX", (0, 0), (-1, -1), 0.5, GRID),
    ]))
    return t


def note(text, bg=NOTE_BG, border=colors.HexColor("#E0B000")):
    t = Table([[P(text, cell)]], colWidths=[W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def steps(items):
    """Numbered steps; an item is text or (text, [flowables shown under it])."""
    out = []
    for item in items:
        if isinstance(item, tuple):
            text, extra = item
            out.append(ListItem([P(text)] + [Spacer(1, 3)] + extra + [Spacer(1, 3)], leftIndent=16))
        else:
            out.append(ListItem(P(item), leftIndent=16))
    return ListFlowable(out, bulletType="1", bulletFontName="Helvetica-Bold", bulletFontSize=10, leftIndent=16,
                        bulletColor=NAVY)


def bullets(items):
    return ListFlowable([ListItem(P(t), leftIndent=12) for t in items], bulletType="bullet", start="•",
                        leftIndent=12, bulletFontSize=9)


def table(rows, widths, header=True):
    data = [[P(c, cell_b if (header and i == 0) else cell) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        data[0] = [P(f'<font color="white">{c}</font>', cell_b) for c in rows[0]]
        t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle(style))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 8 * mm, A4[0], 8 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(18 * mm, A4[1] - 5.5 * mm, "FightIQ  |  Weekly guide")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


story = [
    Spacer(1, 4 * mm),
    P("FightIQ Weekly Guide", title),
    P("How to run the UFC model each fight week, read the picks, and track results. Written for a Mac.", subtitle),
    Spacer(1, 6),
    note("<b>The short version.</b> Close Excel, open Terminal from GitHub Desktop, run "
         "<font face='Courier-Bold'>python3 -m fightiq workbook --upcoming</font>, then open the workbook. "
         "The card, odds, picks, bet suggestions and tracking all fill in by themselves. "
         "Last week's results fill in on the next run."),

    P("1. One-time setup", h1),
    P("Most of this is done already. The only new step is the free odds key.", small),
    Spacer(1, 4),
    steps([
        "Python is installed and the code is cloned with GitHub Desktop to <b>Documents/GitHub/FightIQ</b> "
        "(branch <b>claude/confident-euler-i3nj2j</b>).",
        ("In GitHub Desktop, click <b>Repository &gt; Open in Terminal</b>, then install the helpers and train "
         "the model:", [cmd("pip3 install -r requirements.txt", "python3 -m fightiq train")]),
        ("<b>Odds key (free):</b> sign up at <b>the-odds-api.com</b> (free plan: 500 requests a month; FightIQ "
         "uses 1 per run). Copy the key from their email and save it:",
         [cmd("python3 -m fightiq odds-key PASTE_YOUR_KEY_HERE")]),
    ]),

    P("2. Every fight week (Thursday or Friday)", h1),
    steps([
        "<b>Save and close</b> the workbook in Excel if it is open.",
        "In <b>GitHub Desktop</b>, click <b>Fetch origin</b>. If it then shows <b>Pull origin</b>, click it "
        "(that downloads any updates).",
        "Click <b>Repository &gt; Open in Terminal</b>.",
        ("Run the weekly command:", [cmd("python3 -m fightiq workbook --upcoming"),
         Spacer(1, 3),
         P("This refreshes every fighter's stats, loads the next UFC card from ufcstats.com, fills in the best "
           "current odds from Australian bookmakers, logs every pick into the Tracking tab, fills in W/L "
           "for earlier bouts, and makes the PDF report (in output/reports). It takes about a minute. A Chrome window may open for a few seconds: leave it, "
           "it closes itself.", small)]),
        ("Open the workbook:", [cmd("open output/FightIQ_Model.xlsx")]),
        "On the <b>Predictor</b> tab, check the card. Any bout with blank odds (columns C and D) was not listed by "
        "the odds service yet: type the <b>decimal</b> odds in yourself. Hover over an odds cell to see which "
        "bookmaker had that price.",
        "Read the <b>Pick</b>, <b>FightIQ P(A)</b> and <b>Bet</b> columns (see section 4).",
        ("<b>If you typed or changed any odds:</b> press Cmd+S, close Excel, and run this so they are logged in "
         "Tracking:", [cmd("python3 -m fightiq workbook")]),
        ("<b>Optional, on fight day:</b> update to the latest prices without changing the card:",
         [cmd("python3 -m fightiq workbook --refresh-odds")]),
    ]),

    P("3. After the event", h1),
    bullets([
        "Nothing to do on the night. The next weekly run fills in <b>W / L</b> for every logged bout once the "
        "results are in the data (usually within a day or two of the event).",
        "Bouts that were scrapped are marked <b>Cancelled</b> and do not count towards the record or ROI.",
        "The top of the <b>Tracking</b> tab shows the running record, hit rate, units staked, profit and ROI.",
    ]),
]

story += [
    KeepTogether([
        P("4. Reading the Predictor", h1),
        table([
            ["Column", "What it means"],
            ["Fighter A / B, Odds A / B", "The bout and the decimal odds for each fighter (yellow cells are inputs)."],
            ["Model P(A)", "Chance Fighter A wins from the stats alone."],
            ["Market P(A)", "The bookmakers' chance for Fighter A, with their margin removed."],
            ["FightIQ P(A)", "The blend of the model and the market. This is the number to use when you have odds."],
            ["Pick / Pick prob.", "The fighter FightIQ favours and how likely it rates them to win."],
            ["Bet / Bet odds / EV", "A bet is suggested only when expected value (EV) is above 5% and neither "
                                    "fighter is making their UFC debut."],
            ["Stake (% bankroll)", "Suggested stake: a quarter of the Kelly amount, never more than 2% of bankroll."],
            ["Notes", "Flags names it can't find in the Fighters tab (misspelling or a debutant)."],
        ], [42 * mm, W - 42 * mm]),
    ]),
    Spacer(1, 4),
    P("Typing fighters by hand: capitals don't matter (<i>jim miller</i> = <i>Jim Miller</i>) but the spelling must "
      "match the <b>Fighters</b> tab, with plain letters and no accents. American odds: +150 = 2.50, -200 = 1.50.",
      small),

    KeepTogether([
        P("5. The Tracking tab", h1),
        table([
            ["Column", "Filled by"],
            ["Date, Event, Fight, Pick, FightIQ prob.", "Automatically, for every bout that has odds"],
            ["Bet on, Bet odds, Stake (units)", "Automatically, when a bet is suggested. 1 unit = 1% of bankroll."],
            ["Pick result, Bet result", "Automatically after the event: W, L, D (draw), NC (no contest) or Cancelled"],
            ["Pick correct, Bet profit", "Excel formulas"],
        ], [62 * mm, W - 62 * mm]),
        Spacer(1, 4),
        bullets([
            "Until a bout is settled, each run refreshes its row with the latest odds and pick. Settled rows never change.",
            "You can add your own rows (W / L by hand). They are kept on every rebuild.",
            "Delete any row you don't want counted. The Figueiredo vs Talbott example row is removed automatically.",
        ]),
    ]),

    P("6. Keep your file safe", h1),
    bullets([
        "<b>Always close Excel before running a command.</b> If Excel still has the old file open and you save it, "
        "the old version overwrites the new one.",
        "Keep the workbook where it is: <b>FightIQ/output/FightIQ_Model.xlsx</b>. Don't rename or move it, or the "
        "weekly run won't find your Tracking rows.",
        "Every run saves a copy of the previous file in <b>output/backup</b>.",
        "The output folder is not uploaded to GitHub. Copy the workbook to iCloud or OneDrive now and then.",
    ]),

    P("7. Once a month", h1),
    bullets([
        "Refit the model on the newest fights: <font face='Courier-Bold'>python3 -m fightiq train</font>",
        "After pulling updates in GitHub Desktop, run <font face='Courier-Bold'>pip3 install -r requirements.txt</font> "
        "once in case the update needs a new helper.",
    ]),
]

story += [
    KeepTogether([
        P("8. If something goes wrong", h1),
        table([
            ["You see", "Do this"],
            ["couldn't find the next event on ufcstats.com",
             "ufcstats blocked the automatic read. In Chrome, open the event's page on ufcstats.com (the one listing "
             "the bouts), press Cmd+S, choose <b>Webpage, HTML Only</b>, save to Downloads. Then type "
             "<font face='Courier-Bold'>python3 -m fightiq workbook --event-html </font> (with a space), drag the "
             "saved file into Terminal, and press Enter."],
            ["no odds API key saved", "Save your key: <font face='Courier-Bold'>python3 -m fightiq odds-key YOUR_KEY</font>"],
            ["monthly quota is used up", "Type the odds in yourself; the free quota resets each month."],
            ["no odds yet for A vs B", "The odds service hasn't listed that bout yet: type its odds in, or run "
                                       "--refresh-odds later."],
            ["can't write ... close it in Excel", "Close the workbook in Excel and run the command again."],
            ["Notes: <i>name</i> not in Fighters", "Check the spelling in the Fighters tab (Cmd+F). If they have never "
                                                   "fought in the UFC, it's a debut: no bet is suggested."],
            ["command not found: python3", "Open a new Terminal window from GitHub Desktop; if it persists, reinstall "
                                           "Python from python.org."],
            ["Results not filling in a week later", "The fight-results data may be running behind. Send Claude a "
                                                    "screenshot of the Terminal output."],
        ], [52 * mm, W - 52 * mm]),
    ]),

    KeepTogether([
        P("Command cheat sheet", h1),
        table([
            ["Command", "What it does"],
            ["<font face='Courier'>python3 -m fightiq workbook --upcoming</font>", "Weekly: next card, odds, tracking, results"],
            ["<font face='Courier'>python3 -m fightiq workbook</font>", "Keep the card; refresh stats, log typed odds, fill results"],
            ["<font face='Courier'>python3 -m fightiq workbook --refresh-odds</font>", "Keep the card; update the odds"],
            ["<font face='Courier'>python3 -m fightiq workbook --event-html FILE</font>", "Card from a page saved in Chrome"],
            ["<font face='Courier'>python3 -m fightiq report</font>", "Make and open the predictions + bets PDF"],
            ["<font face='Courier'>python3 -m fightiq odds-key KEY</font>", "Save your odds key (once)"],
            ["<font face='Courier'>python3 -m fightiq train</font>", "Refit the model (monthly)"],
            ["<font face='Courier'>open output/FightIQ_Model.xlsx</font>", "Open the workbook"],
        ], [92 * mm, W - 92 * mm]),
    ]),
    Spacer(1, 8),
    note("<b>A reminder on staking.</b> The backtest edge comes from historical odds whose timing isn't documented, "
         "so treat it as promising rather than proven. Keep stakes small (the sheet caps them at 2%) and let the "
         "Tracking tab build a live record before sizing up.", bg=colors.HexColor("#FDECEC"), border=ACCENT),
]

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=16 * mm, title="FightIQ Weekly Guide", author="FightIQ")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
