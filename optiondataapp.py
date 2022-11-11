import datetime
import collections
import inspect

import logging
import time
import threading

from ibapi import wrapper
from ibapi.client import EClient
from ibapi.utils import longMaxString
from ibapi.utils import iswrapper

# types
from ibapi.common import * # @UnusedWildImport
from ibapi.order_condition import * # @UnusedWildImport
from ibapi.contract import * # @UnusedWildImport
from ibapi.order import * # @UnusedWildImport
from ibapi.order_state import * # @UnusedWildImport
from ibapi.execution import Execution
from ibapi.execution import ExecutionFilter
from ibapi.commission_report import CommissionReport
from ibapi.ticktype import * # @UnusedWildImport
from ibapi.tag_value import TagValue

from ibapi.account_summary_tags import *

from ibapi.scanner import ScanData

def printinstance(inst:Object):
    attrs = vars(inst)
    #print(', '.join('{}:{}'.format(key, decimalMaxString(value) if type(value) is Decimal else value) for key, value in attrs.items()))
    print(', '.join('{}:{}'.format(key, decimalMaxString(value) if type(value) is Decimal else
                                   floatMaxString(value) if type(value) is float else
                                   intMaxString(value) if type(value) is int else  
                                   value) for key, value in attrs.items()))

contract = Contract()
contract.symbol = "GOOG"
contract.secType = "OPT"
contract.exchange = "SMART"
#contract.primaryExchange = "CBOE2"
contract.currency = "USD"
contract.lastTradeDateOrContractMonth = "20221118"
contract.strike = 99
contract.right = "C"
contract.multiplier = "100"

class AppWrapper(wrapper.EWrapper):
    # ! [ewrapperimpl]
    def __init__(self):
        wrapper.EWrapper.__init__(self)

        self.wrapMeth2callCount = collections.defaultdict(int)
        self.wrapMeth2reqIdIdx = collections.defaultdict(lambda: -1)
        self.reqId2nAns = collections.defaultdict(int)
        self.setupDetectWrapperReqId()

    # TODO: see how to factor this out !!

    def countWrapReqId(self, methName, fn):
        def countWrapReqId_(*args, **kwargs):
            self.wrapMeth2callCount[methName] += 1
            idx = self.wrapMeth2reqIdIdx[methName]
            if idx >= 0:
                self.reqId2nAns[args[idx]] += 1
            return fn(*args, **kwargs)

        return countWrapReqId_

    def setupDetectWrapperReqId(self):

        methods = inspect.getmembers(wrapper.EWrapper, inspect.isfunction)
        for (methName, meth) in methods:
            self.wrapMeth2callCount[methName] = 0
            # logging.debug("meth %s", name)
            sig = inspect.signature(meth)
            for (idx, pnameNparam) in enumerate(sig.parameters.items()):
                (paramName, param) = pnameNparam # @UnusedVariable
                # we want to count the errors as 'error' not 'answer'
                if 'error' not in methName and paramName == "reqId":
                    self.wrapMeth2reqIdIdx[methName] = idx

            setattr(AppWrapper, methName, self.countWrapReqId(methName, meth))

            # print("TestClient.wrapMeth2reqIdIdx", self.wrapMeth2reqIdIdx)

# ! [socket_declare]
class AppClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)
        # ! [socket_declare]

        # how many times a method is called to see test coverage
        self.clntMeth2callCount = collections.defaultdict(int)
        self.clntMeth2reqIdIdx = collections.defaultdict(lambda: -1)
        self.reqId2nReq = collections.defaultdict(int)
        self.setupDetectReqId()

    def countReqId(self, methName, fn):
        def countReqId_(*args, **kwargs):
            self.clntMeth2callCount[methName] += 1
            idx = self.clntMeth2reqIdIdx[methName]
            if idx >= 0:
                sign = -1 if 'cancel' in methName else 1
                self.reqId2nReq[sign * args[idx]] += 1
            return fn(*args, **kwargs)

        return countReqId_

    def setupDetectReqId(self):

        methods = inspect.getmembers(EClient, inspect.isfunction)
        for (methName, meth) in methods:
            if methName != "send_msg":
                # don't screw up the nice automated logging in the send_msg()
                self.clntMeth2callCount[methName] = 0
                # logging.debug("meth %s", name)
                sig = inspect.signature(meth)
                for (idx, pnameNparam) in enumerate(sig.parameters.items()):
                    (paramName, param) = pnameNparam # @UnusedVariable
                    if paramName == "reqId":
                        self.clntMeth2reqIdIdx[methName] = idx

                setattr(AppClient, methName, self.countReqId(methName, meth))

                # print("TestClient.clntMeth2reqIdIdx", self.clntMeth2reqIdIdx)



class OptionDataApp(AppWrapper, AppClient):
    def __init__(self):
        AppWrapper.__init__(self)
        AppClient.__init__(self, wrapper=self)

        self.curReqId = 1

        self.tickers = ['GOOG', 'MSFT']

        # ! [socket_init]
        self.nKeybInt = 0
        self.started = False
        self.nextValidOrderId = None
        self.permId2ord = {}
        self.reqId2nErr = collections.defaultdict(int)
        self.globalCancelOnly = False
        self.simplePlaceOid = None

    def getNextReqId(self):
        out = self.curReqId
        self.curReqId += 1
        return out

    def dumpTestCoverageSituation(self):
        for clntMeth in sorted(self.clntMeth2callCount.keys()):
            logging.debug("ClntMeth: %-30s %6d" % (clntMeth,
                                                   self.clntMeth2callCount[clntMeth]))

        for wrapMeth in sorted(self.wrapMeth2callCount.keys()):
            logging.debug("WrapMeth: %-30s %6d" % (wrapMeth,
                                                   self.wrapMeth2callCount[wrapMeth]))

    def dumpReqAnsErrSituation(self):
        logging.debug("%s\t%s\t%s\t%s" % ("ReqId", "#Req", "#Ans", "#Err"))
        for reqId in sorted(self.reqId2nReq.keys()):
            nReq = self.reqId2nReq.get(reqId, 0)
            nAns = self.reqId2nAns.get(reqId, 0)
            nErr = self.reqId2nErr.get(reqId, 0)
            logging.debug("%d\t%d\t%s\t%d" % (reqId, nReq, nAns, nErr))

    @iswrapper
    # ! [connectack]
    def connectAck(self):
        if self.asynchronous:
            self.startApi()

    # ! [connectack]

    @iswrapper
    # ! [nextvalidid]
    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)

        logging.debug("setting nextValidOrderId: %d", orderId)
        self.nextValidOrderId = orderId
        print("NextValidId:", orderId)
    # ! [nextvalidid]

        # we can start now
        if hasattr(self, 'account'):
            self.start()

    @iswrapper
    # ! [managedaccounts]
    def managedAccounts(self, accountsList: str):
        super().managedAccounts(accountsList)
        print("Account list:", accountsList)
        # ! [managedaccounts]

        self.account = accountsList.split(",")[0]
        
        if self.nextValidOrderId is not None:
            self.start()

    def start(self):
        if self.started:
            return

        self.started = True

        if self.globalCancelOnly:
            print("Executing GlobalCancel only")
            self.reqGlobalCancel()
        else:
            print("Executing requests")
            #self.tickOptionComputations_req()
            #self.historicalDataOperations_req()
            self.fetchOptionChains()
            print("Executing requests ... finished")

    def keyboardInterrupt(self):
        self.nKeybInt += 1
        if self.nKeybInt == 1:
            self.stop()
        else:
            print("Finishing test")
            self.done = True

    def stop(self):
        print("Executing cancels")
        #self.tickOptionComputations_cancel()
        #self.historicalDataOperations_cancel()
        print("Executing cancels ... finished")

    def nextOrderId(self):
        oid = self.nextValidOrderId
        self.nextValidOrderId += 1
        return oid

    
    def fetchOptionChains(self):
        for ticker in self.tickers:
            # get contracts first
            con = Contract()
            con.symbol = ticker
            con.secType = "STK"
            con.exchange = "SMART"
            con.currency = "USD"
            reqId = self.getNextReqId()
            self.reqContractDetails(reqId, con)

    @iswrapper
    # ! [error]
    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson = ""):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        if advancedOrderRejectJson:
            print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString, "AdvancedOrderRejectJson:", advancedOrderRejectJson)
        else:
            print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString)

    # ! [error] self.reqId2nErr[reqId] += 1


    @iswrapper
    def winError(self, text: str, lastError: int):
        super().winError(text, lastError)


    def marketDataTypeOperations(self):
        # ! [reqmarketdatatype]
        # Switch to live (1) frozen (2) delayed (3) delayed frozen (4).
        self.reqMarketDataType(MarketDataTypeEnum.REALTIME)
        # ! [reqmarketdatatype]

    @iswrapper
    # ! [marketdatatype]
    def marketDataType(self, reqId: TickerId, marketDataType: int):
        super().marketDataType(reqId, marketDataType)
        print("MarketDataType. ReqId:", reqId, "Type:", marketDataType)
    # ! [marketdatatype]



    def tickOptionComputations_req(self):
        self.reqMarketDataType(MarketDataTypeEnum.REALTIME)
        # Requesting options computations
        # ! [reqoptioncomputations]
        self.reqMktData(1000, contract, "", False, False, [])
        # ! [reqoptioncomputations]


    def tickOptionComputations_cancel(self):
        # Canceling options computations
        # ! [canceloptioncomputations]
        self.cancelMktData(1000)
        # ! [canceloptioncomputations]

    @iswrapper
    # ! [tickprice]
    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float,
                  attrib: TickAttrib):
        super().tickPrice(reqId, tickType, price, attrib)
        print("TickPrice. TickerId:", reqId, "tickType:", tickType,
              "Price:", floatMaxString(price), "CanAutoExecute:", attrib.canAutoExecute,
              "PastLimit:", attrib.pastLimit, end=' ')
        if tickType == TickTypeEnum.BID or tickType == TickTypeEnum.ASK:
            print("PreOpen:", attrib.preOpen)
        else:
            print()
    # ! [tickprice]

    @iswrapper
    # ! [ticksize]
    def tickSize(self, reqId: TickerId, tickType: TickType, size: Decimal):
        super().tickSize(reqId, tickType, size)
        print("TickSize. TickerId:", reqId, "TickType:", tickType, "Size: ", decimalMaxString(size))
    # ! [ticksize]

    @iswrapper
    # ! [tickgeneric]
    def tickGeneric(self, reqId: TickerId, tickType: TickType, value: float):
        super().tickGeneric(reqId, tickType, value)
        print("TickGeneric. TickerId:", reqId, "TickType:", tickType, "Value:", floatMaxString(value))
    # ! [tickgeneric]

    @iswrapper
    # ! [tickstring]
    def tickString(self, reqId: TickerId, tickType: TickType, value: str):
        super().tickString(reqId, tickType, value)
        print("TickString. TickerId:", reqId, "Type:", tickType, "Value:", value)
    # ! [tickstring]

    @iswrapper
    # ! [ticksnapshotend]
    def tickSnapshotEnd(self, reqId: int):
        super().tickSnapshotEnd(reqId)
        print("TickSnapshotEnd. TickerId:", reqId)
    # ! [ticksnapshotend]

    @iswrapper
    # ! [tickbytickalllast]
    def tickByTickAllLast(self, reqId: int, tickType: int, time: int, price: float,
                          size: Decimal, tickAtrribLast: TickAttribLast, exchange: str,
                          specialConditions: str):
        super().tickByTickAllLast(reqId, tickType, time, price, size, tickAtrribLast,
                                  exchange, specialConditions)
        if tickType == 1:
            print("Last.", end='')
        else:
            print("AllLast.", end='')
        print(" ReqId:", reqId,
              "Time:", datetime.datetime.fromtimestamp(time).strftime("%Y%m%d-%H:%M:%S"),
              "Price:", floatMaxString(price), "Size:", decimalMaxString(size), "Exch:" , exchange,
              "Spec Cond:", specialConditions, "PastLimit:", tickAtrribLast.pastLimit, "Unreported:", tickAtrribLast.unreported)
    # ! [tickbytickalllast]

    @iswrapper
    # ! [tickbytickbidask]
    def tickByTickBidAsk(self, reqId: int, time: int, bidPrice: float, askPrice: float,
                         bidSize: Decimal, askSize: Decimal, tickAttribBidAsk: TickAttribBidAsk):
        super().tickByTickBidAsk(reqId, time, bidPrice, askPrice, bidSize,
                                 askSize, tickAttribBidAsk)
        print("BidAsk. ReqId:", reqId,
              "Time:", datetime.datetime.fromtimestamp(time).strftime("%Y%m%d-%H:%M:%S"),
              "BidPrice:", floatMaxString(bidPrice), "AskPrice:", floatMaxString(askPrice), "BidSize:", decimalMaxString(bidSize),
              "AskSize:", decimalMaxString(askSize), "BidPastLow:", tickAttribBidAsk.bidPastLow, "AskPastHigh:", tickAttribBidAsk.askPastHigh)
    # ! [tickbytickbidask]

    # ! [tickbytickmidpoint]
    @iswrapper
    def tickByTickMidPoint(self, reqId: int, time: int, midPoint: float):
        super().tickByTickMidPoint(reqId, time, midPoint)
        print("Midpoint. ReqId:", reqId,
              "Time:", datetime.datetime.fromtimestamp(time).strftime("%Y%m%d-%H:%M:%S"),
              "MidPoint:", floatMaxString(midPoint))
    # ! [tickbytickmidpoint]

    def historicalDataOperations_req(self):
        # Requesting historical data
        # ! [reqHeadTimeStamp]
        #self.reqHeadTimeStamp(4101, ContractSamples.USStockAtSmart(), "TRADES", 0, 1)
        # ! [reqHeadTimeStamp]

        # ! [reqhistoricaldata]
        #queryTime = (datetime.datetime.today() - datetime.timedelta(days=180)).strftime("%Y%m%d %H:%M:%S")
        queryTime=datetime.datetime.today().strftime("%Y%m%d %H:%M:%S")
        self.reqHistoricalData(4102, contract, queryTime,
                                "1 M", "1 min", "TRADES", 1, 1, False, [])
        #self.reqHistoricalData(4103, ContractSamples.EuropeanStock(), queryTime,
                            #    "10 D", "1 min", "TRADES", 1, 1, False, [])
        #self.reqHistoricalData(4104, ContractSamples.EurGbpFx(), "",
                            #    "1 M", "1 day", "MIDPOINT", 1, 1, True, [])
        #self.reqHistoricalData(4103, ContractSamples.USStockAtSmart(), queryTime,
                            #    "1 M", "1 day", "SCHEDULE", 1, 1, False, [])
        # ! [reqhistoricaldata]


    def historicalDataOperations_cancel(self):
        # ! [cancelHeadTimestamp]
        self.cancelHeadTimeStamp(4101)
        # ! [cancelHeadTimestamp]
        
        # Canceling historical data requests
        # ! [cancelhistoricaldata]
        self.cancelHistoricalData(4102)
        self.cancelHistoricalData(4103)
        self.cancelHistoricalData(4104)
        # ! [cancelhistoricaldata]


    def historicalTicksOperations(self):
        # ! [reqhistoricalticks]
        #self.reqHistoricalTicks(18001, ContractSamples.USStockAtSmart(),
                                # "20170712 21:39:33 US/Eastern", "", 10, "TRADES", 1, True, [])
        #self.reqHistoricalTicks(18002, ContractSamples.USStockAtSmart(),
                                # "20170712 21:39:33 US/Eastern", "", 10, "BID_ASK", 1, True, [])
        #self.reqHistoricalTicks(18003, ContractSamples.USStockAtSmart(),
                                # "20170712 21:39:33 US/Eastern", "", 10, "MIDPOINT", 1, True, [])
        # ! [reqhistoricalticks]
        pass

    @iswrapper
    # ! [headTimestamp]
    def headTimestamp(self, reqId:int, headTimestamp:str):
        print("HeadTimestamp. ReqId:", reqId, "HeadTimeStamp:", headTimestamp)
    # ! [headTimestamp]

    @iswrapper
    # ! [historicaldata]
    def historicalData(self, reqId:int, bar: BarData):
        print("HistoricalData. ReqId:", reqId, "BarData.", bar)
    # ! [historicaldata]

    @iswrapper
    # ! [historicaldataend]
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        super().historicalDataEnd(reqId, start, end)
        print("HistoricalDataEnd. ReqId:", reqId, "from", start, "to", end)
    # ! [historicaldataend]

    @iswrapper
    # ! [historicalDataUpdate]
    def historicalDataUpdate(self, reqId: int, bar: BarData):
        print("HistoricalDataUpdate. ReqId:", reqId, "BarData.", bar)
    # ! [historicalDataUpdate]

    @iswrapper
    # ! [historicalticks]
    def historicalTicks(self, reqId: int, ticks: ListOfHistoricalTick, done: bool):
        for tick in ticks:
            print("HistoricalTick. ReqId:", reqId, tick)
    # ! [historicalticks]

    @iswrapper
    # ! [historicalticksbidask]
    def historicalTicksBidAsk(self, reqId: int, ticks: ListOfHistoricalTickBidAsk,
                              done: bool):
        for tick in ticks:
            print("HistoricalTickBidAsk. ReqId:", reqId, tick)
    # ! [historicalticksbidask]

    @iswrapper
    # ! [historicaltickslast]
    def historicalTicksLast(self, reqId: int, ticks: ListOfHistoricalTickLast,
                            done: bool):
        for tick in ticks:
            print("HistoricalTickLast. ReqId:", reqId, tick)
    # ! [historicaltickslast]

    def getOptionChain(self, exchange: str, underlyingConId: int, tradingClass: str, multiplier: str,
                       expirations: SetOfString, strikes: SetOfFloat):
        count = 0
        opt = {'C','P'}
        for o in opt:
            for exp in expirations:
                print(o, exp, "STRIKES", len(strikes))
                for strike in strikes:
                    c = Contract()
                    c.symbol = tradingClass
                    c.secType = "OPT"
                    c.exchange = exchange
                    #c.primaryExchange = "CBOE2"
                    c.currency = "USD"
                    c.lastTradeDateOrContractMonth = exp
                    c.strike = strike
                    c.right = o
                    c.multiplier = multiplier
                    self.reqMktData(self.getNextReqId(), c, "", False, False, [])
                    count += 1
                    if count > 45:
                        #print("!!! SLEEP FIRST TO AVOID OVERLOAD IB !!!")
                        time.sleep(1)
                        count = 0

    @iswrapper
    # ! [securityDefinitionOptionParameter]
    def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                          underlyingConId: int, tradingClass: str, multiplier: str,
                                          expirations: SetOfString, strikes: SetOfFloat):
        super().securityDefinitionOptionParameter(reqId, exchange,
                                                  underlyingConId, tradingClass, multiplier, expirations, strikes)
        if exchange == "SMART":
            print("SMART OPTIONS:")
            print("SecurityDefinitionOptionParameter.",
                "ReqId:", reqId, "Exchange:", exchange, "Underlying conId:", intMaxString(underlyingConId), "TradingClass:", tradingClass, "Multiplier:", multiplier,
                "Expirations:", expirations, "Strikes:", str(strikes))
            th = threading.Thread(target=self.getOptionChain, args=(exchange, underlyingConId, tradingClass, multiplier, expirations, strikes))
            th.start()
    # ! [securityDefinitionOptionParameter]

    @iswrapper
    # ! [securityDefinitionOptionParameterEnd]
    def securityDefinitionOptionParameterEnd(self, reqId: int):
        super().securityDefinitionOptionParameterEnd(reqId)
        print("SecurityDefinitionOptionParameterEnd. ReqId:", reqId)
    # ! [securityDefinitionOptionParameterEnd]

    @iswrapper
    # ! [tickoptioncomputation]
    def tickOptionComputation(self, reqId: TickerId, tickType: TickType, tickAttrib: int,
                              impliedVol: float, delta: float, optPrice: float, pvDividend: float,
                              gamma: float, vega: float, theta: float, undPrice: float):
        super().tickOptionComputation(reqId, tickType, tickAttrib, impliedVol, delta,
                                      optPrice, pvDividend, gamma, vega, theta, undPrice)
        print("TickOptionComputation. TickerId:", reqId, "TickType:", tickType,
              "TickAttrib:", tickAttrib,
              "ImpliedVolatility:", impliedVol, "Delta:", delta, "OptionPrice:",
              optPrice, "pvDividend:", pvDividend, "Gamma: ", gamma, "Vega:", vega,
              "Theta:", theta, "UnderlyingPrice:", undPrice)

    # ! [tickoptioncomputation]



    def contractOperations(self):
        # ! [reqmatchingsymbols]
        self.reqMatchingSymbols(218, "IBM")
        # ! [reqmatchingsymbols]


    @iswrapper
    # ! [contractdetails]
    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        super().contractDetails(reqId, contractDetails)
        printinstance(contractDetails)

        if contractDetails.contract.symbol in self.tickers:
            print("!!! Found Contract !!!")
            self.reqSecDefOptParams(self.getNextReqId(), contractDetails.contract.symbol, "", "STK", contractDetails.contract.conId)
    # ! [contractdetails]

    @iswrapper
    # ! [contractdetailsend]
    def contractDetailsEnd(self, reqId: int):
        super().contractDetailsEnd(reqId)
        print("ContractDetailsEnd. ReqId:", reqId)
    # ! [contractdetailsend]

    @iswrapper
    # ! [symbolSamples]
    def symbolSamples(self, reqId: int,
                      contractDescriptions: ListOfContractDescription):
        super().symbolSamples(reqId, contractDescriptions)
        print("Symbol Samples. Request Id: ", reqId)

        for contractDescription in contractDescriptions:
            derivSecTypes = ""
            for derivSecType in contractDescription.derivativeSecTypes:
                derivSecTypes += " "
                derivSecTypes += derivSecType
            print("Contract: conId:%s, symbol:%s, secType:%s primExchange:%s, "
                  "currency:%s, derivativeSecTypes:%s, description:%s, issuerId:%s" % (
                contractDescription.contract.conId,
                contractDescription.contract.symbol,
                contractDescription.contract.secType,
                contractDescription.contract.primaryExchange,
                contractDescription.contract.currency, derivSecTypes,
                contractDescription.contract.description,
                contractDescription.contract.issuerId))
    # ! [symbolSamples]

    @iswrapper
    # ! [smartcomponents]
    def smartComponents(self, reqId:int, smartComponentMap:SmartComponentMap):
        super().smartComponents(reqId, smartComponentMap)
        print("SmartComponents:")
        for smartComponent in smartComponentMap:
            print("SmartComponent.", smartComponent)
    # ! [smartcomponents]

    @iswrapper
    # ! [tickReqParams]
    def tickReqParams(self, tickerId:int, minTick:float,
                      bboExchange:str, snapshotPermissions:int):
        super().tickReqParams(tickerId, minTick, bboExchange, snapshotPermissions)
        print("TickReqParams. TickerId:", tickerId, "MinTick:", floatMaxString(minTick),
              "BboExchange:", bboExchange, "SnapshotPermissions:", intMaxString(snapshotPermissions))
    # ! [tickReqParams]
