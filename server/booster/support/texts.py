from __future__ import annotations

def _link(locale: str, page: str, label: str) -> str:
    return f'<a href="/support/{locale}/{page}">{label}</a>'

LABELS = {
    "fr": {
        "notifications": "Notifications",
        "tab_all": "Générales",
        "tab_you": "Personnelles",
        "more": "Lire la suite >",
        "no_message": "Vous n'avez reçu aucune notification pour l'instant.",
        "no_news": "Aucune notification pour l'instant.",
        "news_gone": "Cette notification n'est plus disponible.",
        "shop_offer": "Cette offre vous attend dans la boutique !",
        "customer_support": "Service consommateurs",
        "abouts": "À propos de cette application",
        "faq": "Questions fréquentes",
        "contact": "Contact",
        "feedback": "Votre avis",
        "about_opentour": "À propos de OpenTour",
        "terms": "Conditions d'utilisation",
        "privacy": "Politique de confidentialité",
        "license": "Informations légales",
        "health": "Santé et sécurité",
        "wallets": "Solde de rubis",
        "tips": "Trucs et astuces",
        "unavailable": "Indisponible",
        "not_found": "Page introuvable",
        "thanks": "Merci !",
        "closed": "OpenTour",
        "box_contents": "Informations importantes et Taux d'apparition",
    },
    "en": {
        "notifications": "Notifications",
        "tab_all": "To All",
        "tab_you": "To You",
        "more": "More >",
        "no_message": "None",
        "no_news": "No notifications yet.",
        "news_gone": "This notification is no longer available.",
        "shop_offer": "This offer is waiting for you in the shop!",
        "customer_support": "Customer Support",
        "abouts": "About This App",
        "faq": "FAQ",
        "contact": "Support",
        "feedback": "Feedback",
        "about_opentour": "About OpenTour",
        "terms": "Terms of Use",
        "privacy": "Privacy Policy",
        "license": "Legal Information",
        "health": "Health and Safety",
        "wallets": "Rubies",
        "tips": "Tips & Tricks",
        "unavailable": "Unavailable",
        "not_found": "Page Not Found",
        "thanks": "Thank you!",
        "closed": "OpenTour",
        "box_contents": "Important Information and Appearance Rates",
    },
}

OPINION_TYPES = {
    "fr": [("value_01", "Commandes"), ("value_02", "Modes de jeu"),
           ("value_03", "Pilotes/karts/ailes (général)"), ("value_04", "Sons et graphismes"),
           ("value_05", "Bugs identifiés"), ("value_06", "Suggestions de fonctionnalités supplémentaires"),
           ("value_07", "Autre (commentaires, etc.)")],
    "en": [("value_01", "Controls"), ("value_02", "Game modes"),
           ("value_03", "Drivers/karts/gliders (general)"), ("value_04", "Sound and graphics"),
           ("value_05", "Known issues"), ("value_06", "Feature suggestions"),
           ("value_07", "Other (comments, etc.)")],
}

OPINION_FORM = {
    "fr": {"intro": "Faites-nous part de votre avis et de vos suggestions à propos de OpenTour.",
           "subject": "Sujet (requis)", "choose": "Choisissez une option.",
           "content": "Votre avis (requis)", "placeholder": "Entrez votre avis ici.",
           "max": "1 000 caractères max.", "send": "Envoyer",
           "note": "Les avis n'appellent pas de réponse. Pour obtenir une réponse, passez par la page Contact."},
    "en": {"intro": "Tell us what you think about OpenTour, and share your suggestions.",
           "subject": "Subject (required)", "choose": "Choose an option.",
           "content": "Your feedback (required)", "placeholder": "Enter your feedback here.",
           "max": "1,000 characters max.", "send": "Send",
           "note": "We do not reply to feedback. If you need an answer, use the Support page."},
}

def menu(lang: str, name: str) -> list[tuple[str, str]]:
    L = LABELS[lang]
    if name == "customer_support":
        return [("zendesk_faqs", L["faq"]), ("inquiries", L["contact"]), ("opinions/new", L["feedback"])]
    return [("about_opentour", L["about_opentour"]), ("terms_of_service", L["terms"]),
            ("privacy_policy", L["privacy"]), ("license", L["license"]),
            ("health_attention", L["health"])]

def page(lang: str, name: str, locale: str) -> str:
    return _PAGES[lang][name](locale)

def _faq_fr(loc: str) -> str:
    return f"""
<h2 id="opentour">Qu'est-ce que OpenTour ?</h2>
<p class="text">OpenTour est un serveur communautaire, gratuit et non officiel, qui permet de continuer à jouer à Mario Kart Tour après la fin du service officiel, le 30 septembre 2026. Il n'est ni affilié à Nintendo, ni approuvé par Nintendo.</p>
<br><h2 id="compte">Comment fonctionne mon compte ?</h2>
<p class="text">Sur OpenTour, les comptes sont <span class="em bold">locaux à ce serveur</span>. Le bouton de connexion du jeu ouvre une page servie par le serveur : choisissez un compte existant ou créez-en un avec un pseudo. Aucun compte externe n'est nécessaire.</p>
<br><h2 id="appareil">Je change d'appareil ou je réinstalle le jeu</h2>
<p class="text">Votre partie est enregistrée sur ce serveur et liée à votre compte local. Sur le nouvel appareil, connectez-vous et choisissez le même compte dans la liste pour la retrouver.</p>
<br><h2 id="courses">Contre qui est-ce que je cours ?</h2>
<p class="text">En solo, vos adversaires sont des fantômes : ceux des autres joueurs de ce serveur et, si l'administrateur l'a installée, la réserve de fantômes officiels rendus anonymes.<br>Les courses en temps réel fonctionnent lorsque ce serveur fait tourner son service temps réel.</p>
<br><h2 id="achats">Peut-on faire des achats ?</h2>
<p class="text">Non. <span class="em bold">Aucun paiement n'est possible sur OpenTour</span> : rien ne s'y vend contre de l'argent réel.</p>
<br><h2 id="erreur">Un code d'erreur s'affiche</h2>
<p class="text">Relancer le jeu suffit souvent. Si l'erreur revient, notez son code (par exemple 806-xxxx) et l'heure, puis signalez-les à l'administrateur de ce serveur : ces deux informations permettent de retrouver le problème dans ses journaux.</p>
<br><h2 id="maj">Faut-il mettre à jour le jeu ?</h2>
<p class="text">Non. Voir {_link(loc, "update_instructions/android", "Mises à jour")}.</p>
<br><h2 id="donnees">Mes données</h2>
<p class="text">Ce que ce serveur enregistre, et pourquoi : {_link(loc, "privacy_policy", "politique de confidentialité")}.</p>
"""

def _faq_en(loc: str) -> str:
    return f"""
<h2 id="opentour">What is OpenTour?</h2>
<p class="text">OpenTour is a free, unofficial, community-run server that lets you keep playing Mario Kart Tour after the official service ended on September 30, 2026. It is not affiliated with or endorsed by Nintendo.</p>
<br><h2 id="compte">How does my account work?</h2>
<p class="text">On OpenTour, accounts are <span class="em bold">local to this server</span>. The game's sign-in button opens a page served by the server: pick an existing account or create one with a nickname. No external account is needed.</p>
<br><h2 id="appareil">I changed devices or reinstalled the game</h2>
<p class="text">Your save is stored on this server and linked to your local account. On the new device, sign in and pick the same account from the list to get it back.</p>
<br><h2 id="courses">Who am I racing against?</h2>
<p class="text">In solo races your opponents are ghosts: those of other players on this server and, if the administrator installed it, the anonymized pool of official ghosts.<br>Real-time races work when this server runs its realtime service.</p>
<br><h2 id="achats">Can I buy things?</h2>
<p class="text">No. <span class="em bold">No payment is possible on OpenTour</span>: nothing is sold for real money.</p>
<br><h2 id="erreur">An error code is displayed</h2>
<p class="text">Restarting the game often fixes it. If the error comes back, write down its code (for example 806-xxxx) and the time, and report them to the administrator of this server: both help find the problem in its logs.</p>
<br><h2 id="maj">Do I need to update the game?</h2>
<p class="text">No. See {_link(loc, "update_instructions/android", "Updates")}.</p>
<br><h2 id="donnees">My data</h2>
<p class="text">What this server stores, and why: {_link(loc, "privacy_policy", "privacy policy")}.</p>
"""

def _contact_fr(loc: str) -> str:
    return f"""
<p class="text">Une question, un bug, un problème de compte ? Contactez l'administrateur de ce serveur, ou laissez un message via {_link(loc, "opinions/new", "Votre avis")}.</p>
<br><p class="text">Pour que l'on puisse vous aider, indiquez si possible <span class="em bold">le code d'erreur affiché, l'heure du problème et le pseudo de votre compte</span>.</p>
<br><p class="text">Beaucoup de réponses se trouvent déjà dans les {_link(loc, "zendesk_faqs", "questions fréquentes")}.</p>
"""

def _contact_en(loc: str) -> str:
    return f"""
<p class="text">A question, a bug, an account problem? Contact the administrator of this server, or leave a message through {_link(loc, "opinions/new", "Feedback")}.</p>
<br><p class="text">To help us help you, please include <span class="em bold">the error code shown, when it happened, and your account nickname</span>.</p>
<br><p class="text">Many answers are already in the {_link(loc, "zendesk_faqs", "FAQ")}.</p>
"""

def _about_fr(loc: str) -> str:
    return """
<p class="text">OpenTour est un serveur communautaire, gratuit et non officiel, qui fait revivre Mario Kart Tour après la fin du service officiel, le 30 septembre 2026. Chaque serveur OpenTour est hébergé par son propre administrateur.</p>
<br><p class="text">OpenTour n'est ni affilié à Nintendo, ni approuvé par Nintendo. Mario Kart Tour, ses personnages et ses contenus appartiennent à leurs détenteurs respectifs.</p>
"""

def _about_en(loc: str) -> str:
    return """
<p class="text">OpenTour is a free, unofficial, community-run server that brings Mario Kart Tour back after the official service ended on September 30, 2026. Each OpenTour server is hosted by its own administrator.</p>
<br><p class="text">OpenTour is not affiliated with or endorsed by Nintendo. Mario Kart Tour, its characters and its content belong to their respective owners.</p>
"""

def _terms_fr(loc: str) -> str:
    return """
<h1>Conditions d'utilisation de OpenTour</h1>
<p>En jouant sur ce serveur OpenTour, vous acceptez les conditions ci-dessous. Elles remplacent le contrat de licence du service officiel, qui a pris fin le 30 septembre 2026.</p>
<h3>1    Le service</h3>
<p>OpenTour est un serveur communautaire, non commercial et non officiel. Il est fourni tel quel, sans garantie de disponibilité ni de conservation des données, et peut évoluer ou s'arrêter à tout moment, sans préavis.</p>
<h3>2    Votre compte</h3>
<p>Votre compte est local à ce serveur et créé avec un pseudo. Vous êtes responsable de l'usage qui en est fait.</p>
<h3>3    Aucun achat</h3>
<p>Rien ne s'achète avec de l'argent réel sur OpenTour. Les rubis et les objets du jeu n'ont aucune valeur monétaire et ne peuvent être ni vendus, ni échangés contre de l'argent.</p>
<h3>4    Règles de jeu</h3>
<p>Il est interdit de tricher, de modifier le jeu pour fausser les classements ou les fantômes, de perturber le service, d'accéder au compte d'un autre joueur, et d'utiliser un pseudo ou un Mii offensant. Les comptes qui ne respectent pas ces règles peuvent être suspendus.</p>
<h3>5    Propriété intellectuelle</h3>
<p>Mario Kart Tour, ses personnages et ses contenus appartiennent à leurs détenteurs respectifs. OpenTour ne revendique aucun droit sur eux et ne distribue pas le jeu lui-même.</p>
<h3>6    Modifications</h3>
<p>Ces conditions peuvent être modifiées ; la version en vigueur est celle affichée sur cette page.</p>
"""

def _terms_en(loc: str) -> str:
    return """
<h1>OpenTour Terms of Use</h1>
<p>By playing on this OpenTour server, you agree to the terms below. They replace the licence agreement of the official service, which ended on September 30, 2026.</p>
<h3>1    The service</h3>
<p>OpenTour is a non-commercial, unofficial, community-run server. It is provided as is, with no guarantee of availability or of data retention, and may change or stop at any time, without notice.</p>
<h3>2    Your account</h3>
<p>Your account is local to this server and created with a nickname. You are responsible for how it is used.</p>
<h3>3    No purchases</h3>
<p>Nothing on OpenTour is sold for real money. Rubies and in-game items have no monetary value and cannot be sold or exchanged for money.</p>
<h3>4    Fair play</h3>
<p>Cheating, modifying the game to tamper with rankings or ghosts, disrupting the service, accessing another player's account, and offensive nicknames or Miis are not allowed. Accounts that break these rules may be suspended.</p>
<h3>5    Intellectual property</h3>
<p>Mario Kart Tour, its characters and its content belong to their respective owners. OpenTour claims no rights over them and does not distribute the game itself.</p>
<h3>6    Changes</h3>
<p>These terms may change; the version in force is the one shown on this page.</p>
"""

def _privacy_fr(loc: str) -> str:
    return f"""
<h1>Politique de confidentialité de OpenTour</h1>
<p>Cette page explique ce que ce serveur OpenTour enregistre à votre sujet, et pourquoi.</p>
<h3>Ce que nous enregistrons</h3>
<p>・Votre compte local : un identifiant généré et le pseudo que vous avez choisi.<br>
・Votre partie : progression, pilotes, karts, ailes, objets, Mii et réglages.<br>
・Vos fantômes de course. <span class="warning"><b>Ils sont montrés aux autres joueurs comme adversaires, avec votre pseudo et votre Mii.</b></span><br>
・Des journaux techniques (adresse IP, date, requête), gardés pour faire fonctionner le service et repérer les abus.</p>
<h3>Ce que nous ne collectons pas</h3>
<p>・Aucune information de paiement, aucune adresse postale, aucun numéro de téléphone, aucune adresse e-mail.<br>
・Aucune publicité ni aucun traceur publicitaire. Rien n'est vendu ni transmis à des tiers.</p>
<h3>Le jeu lui-même</h3>
<p>L'application, telle que publiée par son éditeur, peut encore envoyer d'elle-même certaines données techniques à des services tiers qu'elle intègre (statistiques d'utilisation, rapports de plantage). OpenTour ne reçoit pas ces données et ne les contrôle pas.</p>
<h3>Vos droits</h3>
<p>Vous pouvez demander à l'administrateur de ce serveur la suppression de votre partie et des données associées : {_link(loc, "inquiries", "contact")}.</p>
"""

def _privacy_en(loc: str) -> str:
    return f"""
<h1>OpenTour Privacy Policy</h1>
<p>This page explains what this OpenTour server stores about you, and why.</p>
<h3>What we store</h3>
<p>・Your local account: a generated identifier and the nickname you chose.<br>
・Your save: progress, drivers, karts, gliders, items, Miis and settings.<br>
・Your race ghosts. <span class="warning"><b>They are shown to other players as opponents, with your nickname and Mii.</b></span><br>
・Technical logs (IP address, time, request), kept to run the service and to detect abuse.</p>
<h3>What we do not collect</h3>
<p>・No payment information, postal address, phone number or email address.<br>
・No advertising and no advertising trackers. Nothing is sold or passed on to third parties.</p>
<h3>The game itself</h3>
<p>The app, as published by its publisher, may still send some technical data on its own to third-party services built into it (usage statistics, crash reports). OpenTour does not receive or control that data.</p>
<h3>Your rights</h3>
<p>You can ask the administrator of this server to delete your save and its associated data: {_link(loc, "inquiries", "contact")}.</p>
"""

_UPDATE = {
    "en": {
        "title": "Updates",
        "lead": "This server runs the final official version of the game (4.0.0). There is nothing to update.",
        "steps": ("Your client was patched once to connect to this server, with the OpenTour build tool.",
                  "If the server address changes, the administrator rebuilds the client and you reinstall it."),
        "notes": ("Your save is stored on the server: reinstalling the game does not lose it.",),
        "notes_title": "Notes:",
    },
    "fr": {
        "title": "Mises à jour",
        "lead": "Ce serveur fait tourner la dernière version officielle du jeu (4.0.0). Il n'y a rien à mettre à jour.",
        "steps": ("Votre client a été patché une fois pour se connecter à ce serveur, avec l'outil de build d'OpenTour.",
                  "Si l'adresse du serveur change, l'administrateur reconstruit le client et vous le réinstallez."),
        "notes": ("Votre partie est enregistrée sur le serveur : réinstaller le jeu ne la fait pas perdre.",),
        "notes_title": "Remarques :",
    },
}

def update_page(locale: str) -> tuple[str, str]:
    t = _UPDATE["fr" if (locale or "").lower()[:2] == "fr" else "en"]
    steps = "".join(f"<li>{s}</li>" for s in t["steps"])
    notes = "<br>".join("・ " + n for n in t["notes"])
    body = (f'<p class="text">{t["lead"]}</p>\n<ol>{steps}</ol>\n'
            f'<p>{t["notes_title"]}<br>{notes}</p>\n')
    return t["title"], body

_UNAVAILABLE = {
    "fr": {
        "my_nintendo_missions": "My Nintendo n'est pas disponible sur OpenTour.",
        "payments": "Aucune transaction payante n'a lieu sur OpenTour : rien ne s'y vend contre de l'argent réel.",
        "wallets": "Votre solde de rubis s'affiche dans le jeu. Sur OpenTour, les rubis ne s'achètent pas : ils se gagnent en jouant.",
        "tips": "Les trucs et astuces ne sont pas encore disponibles.",
        "not_found": "Cette page n'existe pas (ou plus) sur OpenTour.",
        "opinion_saved": "Votre avis a bien été envoyé. Merci de nous aider à améliorer OpenTour !",
        "opinion_invalid": "Choisissez un sujet et écrivez votre avis, puis réessayez.",
        "closed": "Le service officiel de Mario Kart Tour a pris fin le 30 septembre 2026. Vous jouez sur OpenTour, un serveur communautaire.",
        "box": "Le contenu de ce tuyau ne peut pas être affiché pour le moment.",
    },
    "en": {
        "my_nintendo_missions": "My Nintendo is not available on OpenTour.",
        "payments": "No paid transactions take place on OpenTour: nothing is sold for real money.",
        "wallets": "Your ruby balance is shown in the game. On OpenTour, rubies cannot be bought: you earn them by playing.",
        "tips": "Tips & Tricks are not available yet.",
        "not_found": "This page does not exist (or no longer exists) on OpenTour.",
        "opinion_saved": "Your feedback has been sent. Thank you for helping us improve OpenTour!",
        "opinion_invalid": "Choose a subject and write your feedback, then try again.",
        "closed": "The official Mario Kart Tour service ended on September 30, 2026. You are playing on OpenTour, a community-run server.",
        "box": "The contents of this pipe cannot be displayed right now.",
    },
}

def notice(lang: str, name: str) -> str:
    return _UNAVAILABLE[lang][name]

_PAGES = {
    "fr": {"faq": _faq_fr, "contact": _contact_fr, "about_opentour": _about_fr,
           "terms": _terms_fr, "privacy": _privacy_fr},
    "en": {"faq": _faq_en, "contact": _contact_en, "about_opentour": _about_en,
           "terms": _terms_en, "privacy": _privacy_en},
}
