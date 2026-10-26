"""Team crests, by OpenLigaDB team id.

OpenLigaDB crest URLs are community-maintained and partly dead, partly on image
hosts like imgur, partly inline base64. These are the crests from the infobox of
each club's Wikipedia article (de for German clubs, en for English ones), they
win over the OpenLigaDB URL. Teams without an entry keep the OpenLigaDB URL.

SVGs over ~200 KB are replaced by a 120 px PNG thumbnail of the same file.
``.github/scripts/check_crests.py`` checks that every URL loads and reports teams of the
current seasons that have no entry (promoted clubs).
"""

CRESTS: dict[int, str] = {
    # Bundesliga 2026/27
    65: (  # 1. FC Köln
        "https://upload.wikimedia.org/wikipedia/commons/0/01/1._FC_Koeln_Logo_2014%E2%80%93.svg"
    ),
    80: (  # 1. FC Union Berlin
        "https://upload.wikimedia.org/wikipedia/commons/4/44/1._FC_Union_Berlin_Logo.svg"
    ),
    81: (  # 1. FSV Mainz 05
        "https://upload.wikimedia.org/wikipedia/commons/9/9e/Logo_Mainz_05.svg"
    ),
    6: (  # Bayer 04 Leverkusen
        "https://upload.wikimedia.org/wikipedia/de/f/f7/Bayer_Leverkusen_Logo.svg"
    ),
    7: (  # Borussia Dortmund
        "https://upload.wikimedia.org/wikipedia/commons/6/67/Borussia_Dortmund_logo.svg"
    ),
    87: (  # Borussia Mönchengladbach
        "https://upload.wikimedia.org/wikipedia/commons/8/81/Borussia_M%C3%B6nchengladbach_logo.svg"
    ),
    91: (  # Eintracht Frankfurt
        "https://upload.wikimedia.org/wikipedia/de/3/32/Logo_Eintracht_Frankfurt_1998.svg"
    ),
    95: (  # FC Augsburg
        "https://upload.wikimedia.org/wikipedia/de/b/b5/Logo_FC_Augsburg.svg"
    ),
    40: (  # FC Bayern München
        "https://upload.wikimedia.org/wikipedia/commons/8/8d/FC_Bayern_M%C3%BCnchen_logo_%282024%29.svg"
    ),
    9: (  # FC Schalke 04
        "https://upload.wikimedia.org/wikipedia/commons/6/6d/FC_Schalke_04_Logo.svg"
    ),
    100: (  # Hamburger SV
        "https://upload.wikimedia.org/wikipedia/commons/f/f7/Hamburger_SV_logo.svg"
    ),
    1635: (  # RB Leipzig
        "https://upload.wikimedia.org/wikipedia/en/0/04/RB_Leipzig_2014_logo.svg"
    ),
    112: (  # SC Freiburg
        "https://upload.wikimedia.org/wikipedia/de/b/bf/SC_Freiburg_Logo.svg"
    ),
    31: (  # SC Paderborn 07
        "https://upload.wikimedia.org/wikipedia/commons/6/67/SC_Paderborn_07_Logo_new.svg"
    ),
    198: (  # SV 07 Elversberg
        "https://upload.wikimedia.org/wikipedia/commons/d/d4/SV_Elversberg_Logo_2021.svg"
    ),
    134: (  # SV Werder Bremen
        "https://upload.wikimedia.org/wikipedia/commons/b/be/SV-Werder-Bremen-Logo.svg"
    ),
    175: (  # TSG Hoffenheim
        "https://upload.wikimedia.org/wikipedia/commons/e/e7/Logo_TSG_Hoffenheim.svg"
    ),
    16: (  # VfB Stuttgart
        "https://upload.wikimedia.org/wikipedia/commons/e/eb/VfB_Stuttgart_1893_Logo.svg"
    ),
    # 2. Bundesliga 2026/27
    199: (  # 1. FC Heidenheim 1846
        "https://upload.wikimedia.org/wikipedia/commons/9/9d/1._FC_Heidenheim_1846.svg"
    ),
    76: (  # 1. FC Kaiserslautern
        "https://upload.wikimedia.org/wikipedia/commons/d/d3/Logo_1_FC_Kaiserslautern.svg"
    ),
    78: (  # 1. FC Magdeburg
        "https://upload.wikimedia.org/wikipedia/commons/8/84/1._FC_Magdeburg.svg"
    ),
    79: (  # 1. FC Nürnberg
        "https://upload.wikimedia.org/wikipedia/commons/f/fa/1._FC_N%C3%BCrnberg_logo.svg"
    ),
    83: (  # DSC Arminia Bielefeld
        "https://upload.wikimedia.org/wikipedia/commons/f/fd/Arminia_Bielefeld_Logo_2021%E2%80%93.svg"
    ),
    177: (  # Dynamo Dresden
        "https://upload.wikimedia.org/wikipedia/commons/e/e1/Logo_SG_Dynamo_Dresden_neu.svg"
    ),
    74: (  # Eintracht Braunschweig
        "https://upload.wikimedia.org/wikipedia/de/4/45/Logo_Eintracht_Braunschweig.svg"
    ),
    93: (  # Energie Cottbus
        "https://upload.wikimedia.org/wikipedia/commons/5/55/Logo_Energie_Cottbus.svg"
    ),
    98: (  # FC St. Pauli
        "https://upload.wikimedia.org/wikipedia/commons/b/b3/Fc_st_pauli_logo.svg"
    ),
    55: (  # Hannover 96
        "https://upload.wikimedia.org/wikipedia/commons/c/cd/Hannover_96_Logo.svg"
    ),
    54: (  # Hertha BSC
        "https://upload.wikimedia.org/wikipedia/commons/8/81/Hertha_BSC_Logo_2012.svg"
    ),
    104: (  # Holstein Kiel
        "https://upload.wikimedia.org/wikipedia/commons/3/30/Holstein_Kiel_Logo.svg"
    ),
    105: (  # Karlsruher SC
        "https://upload.wikimedia.org/wikipedia/commons/c/c8/Karlsruher_SC_Logo_2.svg"
    ),
    115: (  # SpVgg Greuther Fürth
        "https://upload.wikimedia.org/wikipedia/commons/b/b1/SpVgg_Greuther_F%C3%BCrth_2017.svg"
    ),
    118: (  # SV Darmstadt 98
        "https://upload.wikimedia.org/wikipedia/commons/e/e5/SV_Darmstadt_98_Logo.svg"
    ),
    129: (  # VfL Bochum
        "https://upload.wikimedia.org/wikipedia/commons/7/72/VfL_Bochum_logo.svg"
    ),
    36: (  # VfL Osnabrück
        "https://upload.wikimedia.org/wikipedia/commons/4/4e/VfL_Osnabrueck_Logo_2021%E2%80%93.svg"
    ),
    131: (  # VfL Wolfsburg
        "https://upload.wikimedia.org/wikipedia/commons/c/c5/VfL_Wolfsburg_logo_2026.svg"
    ),
    # Premier League 2026/27
    2942: (  # AFC Bournemouth
        "https://upload.wikimedia.org/wikipedia/en/e/e5/AFC_Bournemouth_%282013%29.svg"
    ),
    438: (  # Aston Villa
        "https://upload.wikimedia.org/wikipedia/en/9/9a/Aston_Villa_FC_new_crest.svg"
    ),
    5967: (  # Brentford F.C.
        "https://upload.wikimedia.org/wikipedia/en/2/2a/Brentford_FC_crest.svg"
    ),
    8790: (  # Brighton & Hove Albion
        "https://upload.wikimedia.org/wikipedia/en/thumb/d/d0/Brighton_and_Hove_Albion_FC_crest.svg/120px-Brighton_and_Hove_Albion_FC_crest.svg.png"
    ),
    364: (  # Chelsea FC
        "https://upload.wikimedia.org/wikipedia/en/c/cc/Chelsea_FC.svg"
    ),
    8795: (  # Coventry City
        "https://upload.wikimedia.org/wikipedia/en/thumb/7/7b/Coventry_City_FC_crest.svg/120px-Coventry_City_FC_crest.svg.png"
    ),
    2489: (  # Crystal Palace FC
        "https://upload.wikimedia.org/wikipedia/en/a/a2/Crystal_Palace_FC_logo_%282022%29.svg"
    ),
    541: (  # Everton FC
        "https://upload.wikimedia.org/wikipedia/en/7/7c/Everton_FC_logo.svg"
    ),
    2617: (  # FC Arsenal
        "https://upload.wikimedia.org/wikipedia/en/5/53/Arsenal_FC.svg"
    ),
    370: (  # FC Liverpool
        "https://upload.wikimedia.org/wikipedia/en/thumb/0/0c/Liverpool_FC.svg/120px-Liverpool_FC.svg.png"
    ),
    549: (  # Fulham FC
        "https://upload.wikimedia.org/wikipedia/en/e/eb/Fulham_FC_%28shield%29.svg"
    ),
    8791: (  # Hull City
        "https://upload.wikimedia.org/wikipedia/en/5/54/Hull_City_A.F.C._logo.svg"
    ),
    8796: (  # Ipswich Town
        "https://upload.wikimedia.org/wikipedia/en/4/43/Ipswich_Town.svg"
    ),
    8792: (  # Leeds United
        "https://upload.wikimedia.org/wikipedia/en/5/54/Leeds_United_F.C._logo.svg"
    ),
    4244: (  # Manchester City
        "https://upload.wikimedia.org/wikipedia/en/e/eb/Manchester_City_FC_badge.svg"
    ),
    2556: (  # Manchester United FC
        "https://upload.wikimedia.org/wikipedia/en/7/7a/Manchester_United_FC_crest.svg"
    ),
    1075: (  # Newcastle United FC
        "https://upload.wikimedia.org/wikipedia/en/8/87/Newcastle_United_FC_2026_crest.svg"
    ),
    8793: (  # Nottingham Forest
        "https://upload.wikimedia.org/wikipedia/en/e/e5/Nottingham_Forest_F.C._logo.svg"
    ),
    8794: (  # Sunderland
        "https://upload.wikimedia.org/wikipedia/en/7/77/Logo_Sunderland.svg"
    ),
    452: (  # Tottenham Hotspur FC
        "https://upload.wikimedia.org/wikipedia/en/b/b4/Tottenham_Hotspur.svg"
    ),
}
