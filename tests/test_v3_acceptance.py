"""End-to-end parser + S1-S5 acceptance command regression."""
from datetime import datetime
from openpyxl import Workbook
from scripts.v3_acceptance import run


def test_acceptance_real_parser_covers_s3_s4_s5_without_double_credit(tmp_path):
    wb=Workbook()
    ws=wb.active
    ws.append(["WO Number","Asset #","WO Status","Primary RFR",
               "Service/Repair Description","Technician","Technician Remarks",
               "Start Date","In Service Date / Time"])
    ws.append(["28025279","VOIDFILL1201","Closed","Preventive Maintenance",
               "PM","Stamps, Ray","Completed",
               datetime(2026,10,23,5,55),datetime(2026,10,23,6,53)])
    ws.append(["28026013","BAG0013","Closed","Process Interruption",
               "Bagger repair","Ssaku, Isaac","RTS",
               datetime(2026,10,23,17,17),datetime(2026,10,23,17,43)])
    ws.append(["28026131","VOIDFILL0501","Completed","Process Interruption",
               "Guide repair","Layne, Jonah","RTS",
               datetime(2026,10,23,21,50),datetime(2026,10,23,22,10)])
    file=tmp_path/"trial.xlsx"
    wb.save(file)
    results=run(file,{"Stamps, Ray":"S3","Ssaku, Isaac":"S4","Layne, Jonah":"S5"})
    assert results["duplicate_completed_ownership"]=={}
    assert results["examples"]["28025279"]==["S3 2026-10-22"]
    assert results["examples"]["28026013"]==["S4 2026-10-23"]
    assert results["examples"]["28026131"]==["S5 2026-10-23"]
