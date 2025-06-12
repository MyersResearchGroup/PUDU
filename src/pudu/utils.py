

thermo_wells = [
'A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','A11','A12',
'B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12',
'C1','C2','C3','C4','C5','C6','C7','C8','C9','C10','C11','C12',
'D1','D2','D3','D4','D5','D6','D7','D8','D9','D10','D11','D12',
'E1','E2','E3','E4','E5','E6','E7','E8','E9','E10','E11','E12',
'F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12',
'G1','G2','G3','G4','G5','G6','G7','G8','G9','G10','G11','G12',
'H1','H2','H3','H4','H5','H6','H7','H8','H9','H10','H11','H12'
]

temp_wells = [
'A1','A2','A3','A4','A5','A6',
'B1','B2','B3','B4','B5','B6',
'C1','C2','C3','C4','C5','C6',
'D1','D2','D3','D4','D5','D6'
]

plate_96_wells = [
'A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','A11','A12',
'B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12',
'C1','C2','C3','C4','C5','C6','C7','C8','C9','C10','C11','C12',
'D1','D2','D3','D4','D5','D6','D7','D8','D9','D10','D11','D12',
'E1','E2','E3','E4','E5','E6','E7','E8','E9','E10','E11','E12',
'F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12',
'G1','G2','G3','G4','G5','G6','G7','G8','G9','G10','G11','G12',
'H1','H2','H3','H4','H5','H6','H7','H8','H9','H10','H11','H12'
]

def liquid_transfer(pipette, volume, source, destination, asp_rate:float=0.5, disp_rate:float=1.0, blow_out:bool=True, touch_tip:bool=False, mix_before:float=0.0, mix_after:float=0.0, mix_reps:int=3, new_tip:bool=True, drop_tip:bool=True):
    if new_tip:
        pipette.pick_up_tip()
    if mix_before > 0:
        pipette.mix(mix_reps, mix_before, source)
    pipette.aspirate(volume, source, rate=asp_rate)
    pipette.dispense(volume, destination, rate=disp_rate)
    if mix_after > 0:
        pipette.mix(mix_reps, mix_after, destination)
    if blow_out: 
        pipette.blow_out()
    if touch_tip:
        pipette.touch_tip()
    if drop_tip:
        pipette.drop_tip() 

#Define slots, to allocate 4 samples in each slot, lasts slots allocate in the border where border effects apply
slot_1 = ['A2', 'B2', 'C2', 'D2']
slot_2 = ['A3', 'B3', 'C3', 'D3']
slot_3 = ['A4', 'B4', 'C4', 'D4']
slot_4 = ['A5', 'B5', 'C5', 'D5']
slot_5 = ['A6', 'B6', 'C6', 'D6']
slot_6 = ['A7', 'B7', 'C7', 'D7']
slot_7 = ['A8', 'B8', 'C8', 'D8']
slot_8 = ['A9', 'B9', 'C9', 'D9']
slot_9 = ['A10', 'B10', 'C10', 'D10']
slot_10 = ['A11', 'B11', 'C11', 'D11']
slot_11 = ['E2', 'F2', 'G2', 'H2']
slot_12 = ['E3', 'F3', 'G3', 'H3']
slot_13 = ['E4', 'F4', 'G4', 'H4']
slot_14 = ['E5', 'F5', 'G5', 'H5']
slot_15 = ['E6', 'F6', 'G6', 'H6']
slot_16 = ['E7', 'F7', 'G7', 'H7']
slot_17 = ['E8', 'F8', 'G8', 'H8']
slot_18 = ['E9', 'F9', 'G9', 'H9']
slot_19 = ['E10', 'F10', 'G10', 'H10']
slot_20 = ['E11', 'F11', 'G11', 'H11']
slot_21 = ['A1', 'B1', 'C1', 'D1']
slot_22 = ['E1', 'F1', 'G1', 'H1']
slot_23 = ['A12', 'B12', 'C12', 'D12']
slot_24 = ['E12', 'F12', 'G12', 'H12']

slots = [slot_1, slot_2, slot_3, slot_4, slot_5, slot_6, slot_7, slot_8, slot_9, slot_10, slot_11, slot_12, slot_13, slot_14, slot_15, slot_16, slot_17, slot_18, slot_19, slot_20, slot_21, slot_22, slot_23, slot_24]

#define rows
row_a = ['A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','A11','A12']
row_b = ['B1','B2','B3','B4','B5','B6','B7','B8','B9','B10','B11','B12']
row_c = ['C1','C2','C3','C4','C5','C6','C7','C8','C9','C10','C11','C12']
row_d = ['D1','D2','D3','D4','D5','D6','D7','D8','D9','D10','D11','D12']
row_e = ['E1','E2','E3','E4','E5','E6','E7','E8','E9','E10','E11','E12']
row_f = ['F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12']
row_g = ['G1','G2','G3','G4','G5','G6','G7','G8','G9','G10','G11','G12']
row_h = ['H1','H2','H3','H4','H5','H6','H7','H8','H9','H10','H11','H12']

rows = [row_a, row_b, row_c, row_d, row_e, row_f, row_g, row_h] 

#define columns        
col_1 = ['A1','B1','C1','D1','E1','F1','G1','H1']
col_2 = ['A2','B2','C2','D2','E2','F2','G2','H2']
col_3 = ['A3','B3','C3','D3','E3','F3','G3','H3']
col_4 = ['A4','B4','C4','D4','E4','F4','G4','H4']
col_5 = ['A5','B5','C5','D5','E5','F5','G5','H5']
col_6 = ['A6','B6','C6','D6','E6','F6','G6','H6']
col_7 = ['A7','B7','C7','D7','E7','F7','G7','H7']
col_8 = ['A8','B8','C8','D8','E8','F8','G8','H8']
col_9 = ['A9','B9','C9','D9','E9','F9','G9','H9']
col_10 = ['A10','B10','C10','D10','E10','F10','G10','H10']
col_11 = ['A11','B11','C11','D11','E11','F11','G11','H11']
col_12 = ['A12','B12','C12','D12','E12','F12','G12','H12']

columns = [col_1, col_2, col_3, col_4, col_5, col_6, col_7, col_8, col_9, col_10, col_11, col_12]

position_to_row_and_column = {'A1':(1,1), 'A2':(1,2), 'A3':(1,3), 'A4':(1,4), 'A5':(1,5), 'A6':(1,6), 'A7':(1,7), 'A8':(1,8), 'A9':(1,9), 'A10':(1,10), 'A11':(1,11), 'A12':(1,12),
                            'B1':(2,1), 'B2':(2,2), 'B3':(2,3), 'B4':(2,4), 'B5':(2,5), 'B6':(2,6), 'B7':(2,7), 'B8':(2,8), 'B9':(2,9), 'B10':(2,10), 'B11':(2,11), 'B12':(2,12),
                            'C1':(3,1), 'C2':(3,2), 'C3':(3,3), 'C4':(3,4), 'C5':(3,5), 'C6':(3,6), 'C7':(3,7), 'C8':(3,8), 'C9':(3,9), 'C10':(3,10), 'C11':(3,11), 'C12':(3,12),
                            'D1':(4,1), 'D2':(4,2), 'D3':(4,3), 'D4':(4,4), 'D5':(4,5), 'D6':(4,6), 'D7':(4,7), 'D8':(4,8), 'D9':(4,9), 'D10':(4,10), 'D11':(4,11), 'D12':(4,12),
                            'E1':(5,1), 'E2':(5,2), 'E3':(5,3), 'E4':(5,4), 'E5':(5,5), 'E6':(5,6), 'E7':(5,7), 'E8':(5,8), 'E9':(5,9), 'E10':(5,10), 'E11':(5,11), 'E12':(5,12),
                            'F1':(6,1), 'F2':(6,2), 'F3':(6,3), 'F4':(6,4), 'F5':(6,5), 'F6':(6,6), 'F7':(6,7), 'F8':(6,8), 'F9':(6,9), 'F10':(6,10), 'F11':(6,11), 'F12':(6,12),
                            'G1':(7,1), 'G2':(7,2), 'G3':(7,3), 'G4':(7,4), 'G5':(7,5), 'G6':(7,6), 'G7':(7,7), 'G8':(7,8), 'G9':(7,9), 'G10':(7,10), 'G11':(7,11), 'G12':(7,12),
                            'H1':(8,1), 'H2':(8,2), 'H3':(8,3), 'H4':(8,4), 'H5':(8,5), 'H6':(8,6), 'H7':(8,7), 'H8':(8,8), 'H9':(8,9), 'H10':(8,10), 'H11':(8,11), 'H12':(8,12)}

row_letter_to_number = {'A':1, 'B':2, 'C':3, 'D':4, 'E':5, 'F':6, 'G':7, 'H':8}


#Xml to uri dictionary
def dictionaryCreatorjson(file):
    import sbol2 as sb2
    from flask import Flask, jsonify
    app = Flask(__name__)
    #given code from website
    doc = sb2.Document()
    doc.read(file)
    #Loops through commponetDefinition
    #for DNA
    # Lists to store different components
    BackBoneList = ['https://charmme.synbiohub.org/user/Gonza10V/CIDARMoCloKit/ComponentDefinition_dvk_backbone_core/1']
    PartsList = ['https://charmme.synbiohub.org/user/Gonza10V/CIDARMoCloKit/J23100/1', 'https://charmme.synbiohub.org/user/Gonza10V/CIDARMoCloKit/E0040m_gfp/1',
    'https://charmme.synbiohub.org/user/Gonza10V/CIDARMoCloKit/B0032/1', 'https://charmme.synbiohub.org/user/Gonza10V/CIDARMoCloKit/B0015/1']
    RestrictionEnzymeList = ['https://charmme.synbiohub.org/user/Gonza10V/ligationtestforreal/ComponentDefinition_BsaI/1']
    # Flags to track duplicates
    gotBone = False
    gotZyme = False
    # Output dictionary
    outputDictionary = {"Parts": [], "Backbone": -1, "Restriction Enzyme": -1}
    # Loop through component definitions
    for cd in doc.componentDefinitions:
        print(cd)
        # Checks backbone
        if cd in BackBoneList:
            print(cd)
            print("backbone")
            if not gotBone:
                outputDictionary["Backbone"] = cd
                gotBone = True  # Update flag
            else:
                print("You have more than one backbone")
                return -1
        # Checks parts
        if cd in PartsList:
            print(cd)
            print("got part")
            outputDictionary["Parts"].append(cd)
        # Checks enzymes
        if cd in RestrictionEnzymeList:
            print(cd)
            print("Enzyme")
            if not gotZyme:
                outputDictionary["Restriction Enzyme"] = cd  # Assign value
                gotZyme = True  # Update flag
            else:
                print("You have more than one restriction enzyme")
                return -1
    # Error Checks
    if len(outputDictionary["Parts"]) <= 1:
        print("Invalid number of parts")
        return -1
    if outputDictionary["Backbone"] == -1:
        print("No Backbone found, try again")
        return -1
    if outputDictionary["Restriction Enzyme"] == -1:
        print("No Restriction Enzyme found, try again")
        return -1
    #(outputDictionary)
    response = jsonify(outputDictionary)
    content = response.get_data(as_text=True)

    return content

#same as before but no Json
#Change to add roles 
def dictionaryCreatorPython(file):
    import sbol2 as sb2
    #given code from website
    doc = sb2.Document()
    doc.read(file)
    #Loops through commponetDefinition
    #for DNA
    # Lists to store different components
    ProductRoleList = ['http://identifiers.org/so/SO:0000804']

    #look at the roles for each
    for cd in doc.componentDefinitions:
        #check for role
        if cd.role in ProductRoleList:
            print(cd)



    # Output dictionary
    outputAssemblies = []
    # Loop through component definitions
    for cd in doc.componentDefinitions:
        print(cd)
        # Checks backbone
        if cd in BackBoneList:
            print(cd)
            print("backbone")
            if not gotBone:
                outputDictionary["Backbone"] = cd
                gotBone = True  # Update flag
            else:
                print("You have more than one backbone")
                return -1
        # Checks parts
        if cd in PartsList:
            print(cd)
            print("got part")
            outputDictionary["Parts"].append(cd)
        # Checks enzymes
        if cd in RestrictionEnzymeList:
            print(cd)
            print("Enzyme")
            if not gotZyme:
                outputDictionary["Restriction Enzyme"] = cd  # Assign value
                gotZyme = True  # Update flag
            else:
                print("You have more than one restriction enzyme")
                return -1
    # Error Checks
    if len(outputDictionary["Parts"]) <= 1:
        print("Invalid number of parts")
        return -1
    if outputDictionary["Backbone"] == -1:
        print("No Backbone found, try again")
        return -1
    if outputDictionary["Restriction Enzyme"] == -1:
        print("No Restriction Enzyme found, try again")
        return -1
    #(outputDictionary)
    response = outputDictionary

    return response


#NEW dictionary creator that goes through xml file 
#reads roles and returns a list of dictionaries
def dictionaryListCreatorPython(file):
    import sbol2 as sb2

    # Disable typed URIs for cleaner URI strings
    sb2.Config.setOption('sbol_typed_uris', False)

    # Read the SBOL document
    doc = sb2.Document()
    doc.read(file)

    # Known SO roles
    PRODUCT_ROLE = 'http://identifiers.org/so/SO:0000804'     # composite product
    PLASMID_ROLE = 'https://identifiers.org/SO:0000755'       # plasmid/backbone

    # SBOL type for Protein
    PROTEIN_TYPE = sb2.BIOPAX_PROTEIN  # URI for proteins

    product_dicts = []

    globalEnzyme = None
    for cd in doc.componentDefinitions:
        # Detect restriction enzyme by Protein type
        #create a filler variable
        if PROTEIN_TYPE in cd.types:
            globalEnzyme = cd.identity

        if PRODUCT_ROLE in cd.roles:
            #create new dict for every product
            result = {
                'Product': cd.identity,
                'Backbone': None,
                'Parts': [],
                'Restriction Enzyme': None
            }

            for comp in cd.components:
                sub_def_uri = comp.definition
                sub_cd = doc.componentDefinitions.get(sub_def_uri)

                if sub_cd is None:
                    continue

                roles = sub_cd.roles
                display_id = sub_cd.displayId or ""

                #print(f"{display_id} roles: {roles} types: {types}")

                # Detect backbone by SO role
                if PLASMID_ROLE in roles:
                    result['Backbone'] = sub_def_uri
                    result['Parts'].append(sub_def_uri)

                # Default case: treat as regular part
                else:
                    result['Parts'].append(sub_def_uri)

            product_dicts.append(result)

        #add enzymes to each dictionary
        for dict in product_dicts:
            print("check for enzyme")
            print(globalEnzyme)
            dict['Restriction Enzyme'] = globalEnzyme

    return product_dicts
