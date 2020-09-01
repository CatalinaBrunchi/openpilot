from numpy import interp

from cereal import car, messaging
from common import op_params
from common.realtime import DT_CTRL
from selfdrive.car import apply_std_steer_torque_limits
from selfdrive.car.hyundai.carstate import GearShifter
from selfdrive.car.hyundai.hyundaican import create_lkas11, create_clu11, create_lfa_mfa
from selfdrive.car.hyundai.values import Buttons, SteerLimitParams, CAR, FEATURES
from opendbc.can.packer import CANPacker
from selfdrive.config import Conversions as CV
from selfdrive.controls.lib.pathplanner import LANE_CHANGE_SPEED_MIN

from common.travis_checker import travis

splmoffsetmphBp = [30, 40, 55, 60, 70]
splmoffsetmphV = [0, 0, 0, 0, 0]

splmoffsetkphBp = [30, 40, 55, 70]
splmoffsetkphV = [0, 0, 0, 0]

VisualAlert = car.CarControl.HUDControl.VisualAlert


def process_hud_alert(enabled, fingerprint, visual_alert, left_lane,
                      right_lane, left_lane_depart, right_lane_depart, lkas_button):

  sys_warning = (visual_alert == VisualAlert.steerRequired) and lkas_button
  if sys_warning:
      sys_warning = 4 if fingerprint in [CAR.HYUNDAI_GENESIS, CAR.GENESIS_G90, CAR.GENESIS_G80] else 3

  if enabled or sys_warning:
      sys_state = 3
  else:
      sys_state = lkas_button

  # initialize to no warnings
  left_lane_warning = 0
  right_lane_warning = 0
  if left_lane_depart and lkas_button:
    left_lane_warning = 1 if fingerprint in [CAR.HYUNDAI_GENESIS, CAR.GENESIS_G90, CAR.GENESIS_G80] else 2
  if right_lane_depart and lkas_button:
    right_lane_warning = 1 if fingerprint in [CAR.HYUNDAI_GENESIS, CAR.GENESIS_G90, CAR.GENESIS_G80] else 2

  return sys_warning, sys_state, left_lane_warning, right_lane_warning


class CarController():
  def __init__(self, dbc_name, CP, VM):
    self.apply_steer_last = 0
    self.car_fingerprint = CP.carFingerprint
    self.steermaxLimit = int(CP.steermaxLimit)
    self.packer = CANPacker(dbc_name)
    self.steer_rate_limited = False
    self.longcontrol = False
    self.manual_steering = False
    self.manual_steering_timer = 0
    self.sm = 0
    self.smartspeed = 0
    self.setspeed = 0
    self.currentspeed = 0
    self.smartspeed_old = 0
    self.smartspeedupdate = False
    self.fixed_offset = 0
    self.button_res_stop = self.button_set_stop = 0
    if not travis:
      self.sm = messaging.SubMaster(['liveMapData'])

  def update(self, enabled, CS, frame, actuators, pcm_cancel_cmd, visual_alert,
             left_lane, right_lane, left_lane_depart, right_lane_depart):

    # *** compute control surfaces ***

    if self.car_fingerprint in FEATURES["send_lfa_mfa"]:
      self.lfa_available = True
    else:
      self.lfa_available = False
    # Steering Torque
    updated_SteerLimitParams = SteerLimitParams
    updated_SteerLimitParams.STEER_MAX = self.steermaxLimit

    new_steer = actuators.steer * updated_SteerLimitParams.STEER_MAX
    apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, updated_SteerLimitParams)
    self.steer_rate_limited = new_steer != apply_steer

    # disable if steer angle reach 90 deg, otherwise mdps fault in some models
    lkas_active = enabled and abs(CS.out.steeringAngle) < 90.

    # fix for Genesis hard fault at low speed
    if CS.out.vEgo < 55 * CV.KPH_TO_MS and self.car_fingerprint == CAR.HYUNDAI_GENESIS and not CS.mdps_bus:
      lkas_active = False

    if enabled and CS.out.steeringPressed and CS.out.vEgo < LANE_CHANGE_SPEED_MIN and (CS.out.leftBlinker or CS.out.rightBlinker):
      self.manual_steering_timer += 1
      if self.manual_steering_timer > 50:
         self.manual_steering = True
         self.manual_steering_timer = 50
    elif (self.manual_steering and not CS.out.leftBlinker and not CS.out.rightBlinker) or not CS.out.vEgo < LANE_CHANGE_SPEED_MIN or not enabled:
      self.manual_steering = False

    if self.manual_steering:
      lkas_active = False

    if not lkas_active:
      apply_steer = 0

    self.apply_steer_last = apply_steer

    sys_warning, sys_state, left_lane_warning, right_lane_warning =\
      process_hud_alert(enabled, self.car_fingerprint, visual_alert,
                        left_lane, right_lane, left_lane_depart, right_lane_depart, CS.lkas_button_on)

    can_sends = []
    can_sends.append(create_lkas11(self.packer, frame, self.car_fingerprint, apply_steer, lkas_active,
                                   CS.lkas11, sys_warning, sys_state, enabled,
                                   left_lane, right_lane,
                                   left_lane_warning, right_lane_warning, self.lfa_available, 0))

    if CS.mdps_bus: # send lkas11 bus 1 if mdps
      clu11_speed = CS.clu11["CF_Clu_Vanz"]
      enabled_speed = 38 if CS.is_set_speed_in_mph else 60
      if clu11_speed > enabled_speed or not lkas_active or CS.out.gearShifter == GearShifter.reverse:
          enabled_speed = clu11_speed
      can_sends.append(create_lkas11(self.packer, frame, self.car_fingerprint, apply_steer, lkas_active,
                                   CS.lkas11, sys_warning, sys_state, enabled,
                                   left_lane, right_lane,
                                   left_lane_warning, right_lane_warning, self.lfa_available, 1))

      can_sends.append(create_clu11(self.packer, frame, 1, CS.clu11, Buttons.NONE, enabled_speed))

    if pcm_cancel_cmd:
      can_sends.append(create_clu11(self.packer, frame, 0, CS.clu11, Buttons.CANCEL, self.currentspeed))
    elif CS.out.cruiseState.standstill and CS.vrelative > 0:
      can_sends.append(create_clu11(self.packer, frame, 0, CS.clu11, Buttons.RES_ACCEL, self.currentspeed))

    # 20 Hz LFA MFA message
    if frame % 5 == 0 and self.lfa_available:
      can_sends.append(create_lfa_mfa(self.packer, frame, enabled))

    # Speed Limit Related Stuff  Lot's of comments for others to understand!

    if not travis:
      self.sm.update(0)
      if self.sm['liveMapData'].speedLimit and enabled:
        if CS.is_set_speed_in_mph:
          self.smartspeed = self.sm['liveMapData'].speedLimit * CV.MS_TO_MPH
          self.fixed_offset = interp(self.smartspeed, splmoffsetmphBp, splmoffsetmphV)
          self.smartspeed = self.smartspeed + int(self.fixed_offset)
          self.smartspeed = max(self.smartspeed, 20)
          self.setspeed = CS.cruisesetspeed * CV.MS_TO_MPH
          self.currentspeed = int(CS.out.vEgo * CV.MS_TO_MPH)
        else:
          self.smartspeed = self.sm['liveMapData'].speedLimit * CV.MS_TO_KPH
          self.fixed_offset = interp(self.smartspeed, splmoffsetkphBp, splmoffsetkphV)
          self.smartspeed = self.smartspeed + int(self.fixed_offset)
          self.smartspeed = max(self.smartspeed, 30)
          self.setspeed = CS.cruisesetspeed * CV.MS_TO_KPH
          self.currentspeed = int(CS.out.vEgo * CV.MS_TO_KPH)
      else:
        self.smartspeed_old = 0

      if self.smartspeed_old != self.smartspeed:
        self.smartspeedupdate = True

      self.smartspeed_old = self.smartspeed

      if enabled and CS.rawcruiseStateenabled and self.smartspeedupdate:
        if (self.setspeed > (self.smartspeed * 1.005)) and (CS.cruise_buttons != 4):
          can_sends.append(create_clu11(self.packer, frame, 0, CS.clu11, Buttons.SET_DECEL, self.currentspeed))
          if CS.cruise_buttons == 1:
             self.button_res_stop += 2
          else:
             self.button_res_stop -= 1
        elif (self.setspeed < (self.smartspeed / 1.005)) and (CS.cruise_buttons != 4):
          can_sends.append(create_clu11(self.packer, frame, 0, CS.clu11, Buttons.RES_ACCEL, self.currentspeed))
          if CS.cruise_buttons == 2:
             self.button_set_stop += 2
          else:
             self.button_set_stop -= 1
        else:
          self.button_res_stop = self.button_set_stop = 0

        if (abs(self.smartspeed - self.setspeed) < 1) or (self.button_res_stop >= 100) or (self.button_set_stop >= 100):
          self.smartspeedupdate = False
      else:
        self.button_set_stop = self.button_res_stop = 0

    return can_sends
