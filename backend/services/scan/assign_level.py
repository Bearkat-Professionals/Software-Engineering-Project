from backend.records.enums import IdentificationLevel
from backend.services.scan.evidence import IDENTIFIED_MIN_SCORE, UNKNOWN_TYPE_ID, UNKNOWN_VENDOR_ID

class Identification:
    def __init__(self, level, device_type_id, vendor_id, device_name, model, score):
        self.level = level
        self.device_type_id = device_type_id
        self.vendor_id = vendor_id
        self.device_name = device_name
        self.model = model
        self.score = score


def assign_level(resolution, score, vendor_id):
    device_type_id = resolution.device_type_id
    type_known = True
    if device_type_id == None or device_type_id == UNKNOWN_TYPE_ID:
        type_known = False
        device_type_id = UNKNOWN_TYPE_ID

    vendor_known = True
    if vendor_id == None or vendor_id == UNKNOWN_VENDOR_ID:
        vendor_known = False
        vendor_id = UNKNOWN_VENDOR_ID


    name, model = pick_NameAndModel(resolution.winning_matches)


    if type_known == True and name != None and score.score >= IDENTIFIED_MIN_SCORE:
        level = IdentificationLevel.IDENTIFIED
    elif type_known == True or vendor_known == True:
        level = IdentificationLevel.PARTIAL
    else:
        level = IdentificationLevel.UNIDENTIFIED



    if level != IdentificationLevel.IDENTIFIED:
        name = None
        model = None

    result = Identification(level, device_type_id, vendor_id, name, model, score)
    return result




def is_stronger(Match_a_WhichIsStronger, Match_b_WhichIsStronger):
    weight_a = Match_a_WhichIsStronger.evidence.weight
    weight_b = Match_b_WhichIsStronger.evidence.weight


    if weight_a > weight_b:
        return True
    if weight_a < weight_b:
        return False

    if Match_a_WhichIsStronger.evidence.signature_id < Match_b_WhichIsStronger.evidence.signature_id:
        return True
    return False



def StrongestToWeakest(matches):
    left_over = list(matches)
    ordered = []

    while len(left_over) > 0:
        strongest = left_over[0]
        for match in left_over:
            if is_stronger(match, strongest):
                strongest = match
        ordered.append(strongest)
        left_over.remove(strongest)

    return ordered

def pick_NameAndModel(winning_matches):
    ordered = StrongestToWeakest(winning_matches)

    name = None
    named_spot = None
    for i in range(len(ordered)):
        if ordered[i].resulting_device_name:
            name = ordered[i].resulting_device_name
            named_spot = i
            break

    if name == None:
        return None, None


    model = None
    for i in range(named_spot, len(ordered)):
        if ordered[i].resulting_model:
            model = ordered[i].resulting_model
            break

    return name, model
