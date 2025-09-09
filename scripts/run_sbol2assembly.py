from pudu.assembly import sbol2_DNA_Assembly
from pudu.utils import assemblyPlan_To_JSON
from opentrons import protocol_api

assembly_JSON = assemblyPlan_To_JSON('../tests/validation_assembly1.xml')

# metadata
metadata = {
'protocolName': 'SBOL2 DNA assembly',
'author': 'Luke Dysart <luke.dysart@colorado.edu>',
'description': 'Automated DNA assembly from SBOL2 Assembly Plan',
'apiLevel': '2.13'}

def run(protocol= protocol_api.ProtocolContext):

    pudu_sbol2_assembly = sbol2_DNA_Assembly(assembly_JSON)
    pudu_sbol2_assembly.run(protocol)
    pudu_sbol2_assembly.get_xlsx_output('sbol2_assembly_output.xlsx')