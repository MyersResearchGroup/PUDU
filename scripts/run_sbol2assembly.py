from pudu.assembly import SBOLLoopAssembly
from pudu.utils import assembly_plan_RDF_to_JSON
from opentrons import protocol_api
from pathlib import Path

# Get the absolute path to the tests directory relative to this script
file_path = Path("output.json")
assembly_JSON = str(file_path)

# metadata
metadata = {
'protocolName': 'SBOL2 DNA assembly',
'author': 'Luke Dysart <luke.dysart@colorado.edu>',
'description': 'Automated DNA assembly from SBOL2 Assembly Plan',
'apiLevel': '2.14'}

def run(protocol= protocol_api.ProtocolContext):

    pudu_sbol2_assembly = SBOLLoopAssembly(assembly_JSON)
    pudu_sbol2_assembly.run(protocol)
    #pudu_sbol2_assembly.get_xlsx_output('sbol2_assembly_output.xlsx')
