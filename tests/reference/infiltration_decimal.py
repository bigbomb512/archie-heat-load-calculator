#!/usr/bin/env python3
"""Offline arithmetic worksheet; synthetic QA only, never a project method.

No Archie imports, gate, network or project writes. Run explicitly to stdout.
50-digit Decimal evaluation of the equations in the decision record is an
independent arithmetic check, not validation of those engineering equations.
"""
from decimal import Decimal as D, localcontext
import json


def properties(db, wb, pressure):
    saturation = D('0.61094') * (D('17.625') * wb / (wb + D('243.04'))).exp()
    vapour = saturation - D('0.00066') * (1 + D('0.00115') * wb) * pressure * (db - wb)
    ratio = D('0.621945') * vapour / (pressure - vapour)
    enthalpy = D('1.006') * db + ratio * (D('2501') + D('1.86') * db)
    volume = D('0.287055') * (db + D('273.15')) * (1 + D('1.607') * ratio) / pressure
    return dict(saturation_kpa=saturation, vapour_kpa=vapour,
                humidity_ratio=ratio, enthalpy_kj_kg=enthalpy, volume_m3_kg=volume)


def case(name, outdoor_db, outdoor_wb, flow='6', factor='1'):
    indoor_db, indoor_wb, pressure = D('24'), D('18'), D('101.325')
    outdoor_db, outdoor_wb, flow, factor = map(D, (outdoor_db, outdoor_wb, flow, factor))
    indoor = properties(indoor_db, indoor_wb, pressure)
    outdoor = properties(outdoor_db, outdoor_wb, pressure)
    mass = flow * factor / 1000 / outdoor['volume_m3_kg']
    sensible = mass * D('1.006') * (outdoor_db - indoor_db)
    total = mass * (outdoor['enthalpy_kj_kg'] - indoor['enthalpy_kj_kg'])
    latent = total - sensible
    # Match the documented reporting precision, not a call to Archie rounding.
    rounded_s = sensible.quantize(D('0.0001'))
    rounded_l = latent.quantize(D('0.0001'))
    applied_s, applied_l = max(rounded_s, D(0)), max(rounded_l, D(0))
    return dict(
        case_id=name,
        inputs=dict(value=flow, unit='L/s', indoor_db_c=indoor_db, indoor_wb_c=indoor_wb,
                    outdoor_db_c=outdoor_db, outdoor_wb_c=outdoor_wb, pressure_kpa=pressure,
                    schedule_factor=factor),
        indoor=indoor, outdoor=outdoor,
        expected=dict(mass_flow_kg_s=mass, applied_flow_lps=flow * factor,
                      signed_sensible_unrounded_kw=sensible, signed_latent_unrounded_kw=latent,
                      signed_total_unrounded_kw=total, raw_signed_sensible_kw=rounded_s,
                      raw_signed_latent_kw=rounded_l, sensible_kw=applied_s,
                      latent_kw=applied_l, total_kw=applied_s + applied_l))


def serializable(value):
    if isinstance(value, D):
        return format(value.quantize(D('0.000000000001')), 'f')
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value


def worksheet():
    with localcontext() as context:
        context.prec = 50
        cases = [
            case('hot_humid', '35', '24'),
            case('hot_dry', '35', '18'),
            case('cool_humid', '20', '19'),
            case('cool_dry', '20', '15'),
            case('same_state', '24', '18'),
            case('same_db_more_moisture', '24', '22'),
            case('schedule_off', '35', '24', factor='0'),
            case('schedule_half', '35', '24', factor='0.5'),
            case('double_flow', '35', '24', flow='12'),
            case('outside_air_100_lps', '35', '24', flow='100'),
            # Synthetic 20 m² and 0.36 ACH; CH numbers are not room assignments.
            case('height_2600', '35', '24', flow='5.2'),
            case('height_2900', '35', '24', flow='5.8'),
            case('height_3150', '35', '24', flow='6.3'),
        ]
        return serializable(dict(
            status='synthetic_test_fixture_only_not_engineering_approval',
            basis='Independent 50-digit Decimal worksheet of documented existing equations',
            project_data=False, cases=cases))


if __name__ == '__main__':
    print(json.dumps(worksheet(), indent=2, sort_keys=True))
