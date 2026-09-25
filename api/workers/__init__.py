"""Workers.

Un worker = une capacité = un modèle résident. Les poids ne sont jamais
chargés à la demande : le temps de démarrage de H3, Kimodo ou
Hunyuan3D-Paint l'interdit. Chaque module expose une fonction dont la
signature est `fn(*, report, **kwargs) -> dict`, où `report(progress,
message)` remonte l'avancement et où le dictionnaire rendu devient le
résultat du travail.
"""
