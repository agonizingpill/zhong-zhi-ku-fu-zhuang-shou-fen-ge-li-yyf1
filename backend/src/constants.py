ROLES=['operator','supervisor','viewer']
REGEN_STATES=['requested','issued','cancelled','harvested']
FLOWER_STATES=['planned','assigned','released']
POLLEN_STATES=['prepared','pollinated']
EMERGENCE_STATES=['recorded','superseded']
RESOW_STATES=['pending_review','approved','rejected']
# 同作物历次出苗样本少于该数，只给参考区间，需主管人工核定需种量。
EMERGENCE_MIN_SAMPLES=5
