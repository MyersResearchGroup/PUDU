from pudu.assembly import Loop_assembly
from opentrons import protocol_api
import sbol3

assembly_1 = {"parts":["j23101_URI", "B0032_URI", "GFP_URI", "B0015_URI" ], "backbone":"Moclo_DVA", "restriction enzyme":"BsaI"}
assembly_2 = {"parts":["j23100_URI", "B0032_URI", "GFP_URI", "B0015_URI" ], "backbone":"Moclo_DVA", "restriction enzyme":"BsaI"}
assemblies = [assembly_1, assembly_2]

# metadata
metadata = {
'protocolName': 'PUDU Loop assembly',
'author': 'Gonzalo Vidal <gsvidal@uc.cl>',
'description': 'Automated DNA assembly Loop protocol',
'apiLevel': '2.13'}

def run(protocol= protocol_api.ProtocolContext):

    pudu_loop_assembly = Loop_assembly(assemblies=assemblies)
    pudu_loop_assembly.run(protocol)
    #chained actions
    #save sbol
    doc = sbol3.Document()
    doc.add(pudu_loop_assembly.sbol_output)
    doc.write('pudu_loop_assembly.nt', sbol3.SORTED_NTRIPLES)
    #save xlsx
    pudu_loop_assembly.get_xlsx_output('pudu_loop_assembly')

