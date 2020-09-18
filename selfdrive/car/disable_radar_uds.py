#!/usr/bin/env python3
from tqdm import tqdm

from panda import Panda
from panda.python.uds import UdsClient, NegativeResponseError, SESSION_TYPE, CONTROL_TYPE, MESSAGE_TYPE

ADDR=0x7d0

if __name__ == "__main__":
  panda = Panda()
  panda.set_safety_mode(Panda.SAFETY_ALLOUTPUT)
  uds_client = UdsClient(panda, ADDR, 2, timeout=0.1, debug=True)

  #uds_client.diagnostic_session_control(SESSION_TYPE.DEFAULT)
  #uds_client.diagnostic_session_control(SESSION_TYPE.EXTENDED_DIAGNOSTIC)

  #uds_client.tester_present(SESSION_TYPE.TESTER_PRESENT)

  #uds_client.communication_control(CONTROL_TYPE.DISABLE_RX_DISABLE_TX, MESSAGE_TYPE.NORMAL)
  #uds_client.communication_control(CONTROL_TYPE.DISABLE_RX_DISABLE_TX, MESSAGE_TYPE.NETWORK_MANAGEMENT)
  #uds_client.communication_control(CONTROL_TYPE.DISABLE_RX_DISABLE_TX, MESSAGE_TYPE.NORMAL_AND_NETWORK_MANAGEMENT)


  # messages that work
  #data = uds_client.communication_control(CONTROL_TYPE.EXTENDED_DIAGNOSTIC, MESSAGE_TYPE.NORMAL_AND_NETWORK_MANAGEMENT)
  #data = uds_client.communication_control(CONTROL_TYPE.DISABLE_RX_DISABLE_TX | 0x80, MESSAGE_TYPE.NORMAL_AND_NETWORK_MANAGEMENT)
  #exit(0)

  print("querying addresses ...")
  l = list(range(0x100))
  with tqdm(total=len(l)) as t:
    for i in l:
     # ct = i >> 8
      mt = i & 0xFF
     # t.set_description(f"{hex(ct)} - {hex(mt)}")
      t.set_description(f"{hex(mt)}")
      try:
        data = uds_client.diagnostic_session_control(mt)
        #data = uds_client.diagnostic_session_control(ct, mt)
        #print(f"\n{ct} - {mt}: success")
        print(f"\n{mt}: success")
      except NegativeResponseError as e:
        if e.message != "COMMUNICATION_CONTROL - sub-function not supported" and e.message != "COMMUNICATION_CONTROL - request out of range":
          #print(f"\n{ct} - {mt}: {e.message}")
          print(f"\n{mt}: {e.message}")
      t.update(1)
