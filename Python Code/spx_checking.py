#%% Importing modules
import smtplib
import pandas as pd
import numpy as np
import datetime as dt
import pandas.stats.moments as st
from pandas import ExcelWriter
import scipy.stats as st
import matplotlib.pyplot as plt
import os
import quandl as qd
import seaborn as sns
from scipy.stats import skewnorm as skn
from scipy.stats import norm

# Function for saving excel files
def save_xls(list_dfs, xls_path, sheet_names):
    writer = ExcelWriter(xls_path)
    for n, df in enumerate(list_dfs):
        df.to_excel(writer, sheet_names[n])
    writer.save()
    return

# Reading in Data
# Reading VIX data from CBOE directly
# VIX is stored as 3 separate files on CBOE's website
#   2004 to present : http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vixcurrent.csv
#   1990 to 2003    : http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vixarchive.xls
#   1986 to 2003 VXO: http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vxoarchive.xls

# First read raw files directly 
vix_present = pd.read_csv('http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vixcurrent.csv').dropna()
# vix_old = pd.read_excel('http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vixarchive.xls').dropna()
vxo_old = pd.read_excel('http://www.cboe.com/publish/scheduledtask/mktdata/datahouse/vxoarchive.xls').dropna()

# Function for cleaning CBOE VIX data
def clean_cboe(df):
    df.columns = ['Date','Open','High','Low','Close']
    df = df[1:]
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index(pd.DatetimeIndex(df['Date']))
    return df[['Open','High','Low','Close']]

# Applying clean_cboe to vix data
vix_present = clean_cboe(vix_present)
# vix_old = clean_cboe(vix_old)
vxo_old = clean_cboe(vxo_old)

# Currently the vix_old dataframe doesn't have the Open prices so VXO will be used to proxy VIX prior
# to 2003
vix = pd.concat([vxo_old,vix_present],axis = 0)

# Reading SKEW Index data directly from CBOE
skew = pd.read_csv('https://www.cboe.com/publish/scheduledtask/mktdata/datahouse/skewdailyprices.csv')
skew_raw = skew.copy()
skew.columns = ['Date','Skew','na1','na2']
skew = skew[1:]
skew['Date'] = pd.to_datetime(skew['Date'])
skew = skew.set_index(pd.DatetimeIndex(skew['Date']))[['Skew']]
skew['skew'] = -(pd.to_numeric(skew['Skew'], downcast='float') - 100)/10
del skew['Skew']


# Reading in SPX Data
os.chdir('C:\\Users\\Fang\\Desktop\\Python Trading\\SPX Option Backtester\\spx_options_backtesting\\SPX Data')
spx = pd.read_csv('SPX.csv')
spx = spx.set_index(pd.DatetimeIndex(spx['Date']))[['Open','High','Low','Close','Adj Close']]


# Joining all index together to one dataframe
spx = spx[['Open','Close']]
spx.columns = ['SPX ' + s for s in spx.columns.tolist()]

vix = vix[['Open','Close']]
vix.columns = ['VIX ' + s for s in vix.columns.tolist()]

#
df = pd.concat([spx,vix,skew],axis = 1).dropna()

# An error in data: 2000-10-18 VIX Close value is a string, converting to float
df['VIX Close'][2714] = 32.5

# Fixing VIX values so that they are floats
df['VIX Close'] = df['VIX Close'].astype('float')
df['VIX Open'] = df['VIX Open'].astype('float')

# Adjusting VIX so that it's on 252 trading days
df['Daily VIX Open'] = np.sqrt(((df['VIX Open']*df['VIX Open'])/365)*1.5)/100
df['Daily VIX Close'] = np.sqrt(((df['VIX Close']*df['VIX Close'])/365)*1.5)/100

# Cleaning up unused dataframes
del skew, spx, vix, vix_present, vxo_old

#%% Worst Returns
def worst_return(vixlvl, dte, price = 'Close', display = True):
    temp_df = df.copy()[['SPX Open','SPX Close','VIX Open','VIX Close']]
    skew = skew_raw.copy()
    skew.columns = ['Date','Skew','na1','na2']
    skew = skew[1:]
    skew['Date'] = pd.to_datetime(skew['Date'])
    skew = skew.set_index(pd.DatetimeIndex(skew['Date']))[['Skew']]
    
    temp_df = pd.concat([temp_df,skew],axis = 1)
    
    temp_df['spx_shift'] = temp_df['SPX Close'].shift(-dte)
    temp_df['vix_shift'] = temp_df['VIX Close'].shift(-dte)
    
    if price == 'Open':
        temp_df['ret'] = temp_df['spx_shift']/temp_df['SPX Open'] - 1
        temp_df = temp_df[temp_df['VIX Open'] <= vixlvl].dropna()
    else:
        temp_df['ret'] = temp_df['spx_shift']/temp_df['SPX Close'] - 1
        temp_df = temp_df[temp_df['VIX Close'] <= vixlvl].dropna()
    
    if display:
        print("Worst Return:")
        print(temp_df.loc[temp_df['ret'].idxmin()])
        print("Return Stats:")
        print(temp_df['ret'].describe())
    return temp_df.sort_values('ret', ascending = True).head()


#%% Creating function to produce the daily x% VaR of SPX

# spx_implied_var: rolling_window, var_pct, mkt_time --> dataframe
#   Function consumes an int, rolling_window, a float,
#   var_pct, and a string, mkt_time, and determines the worst 
#   case return using the skewnorm function and applying
#   VIX as the scaling parameter and SKEW as the shape parameter

def spx_implied_var(rolling_window, var_pct, mkt_time = 'Close', option = 'P'):
    
    # Here it's specifying to use the market Open values so that
    # the worst case will be from market open on trade date to
    # market close on expiry
    if mkt_time == 'Open':
        temp_df = df[['SPX Open','SPX Close','skew',
                      'Daily VIX Open','Daily VIX Close','VIX Close']]
        temp_df['spx_shift'] = temp_df['SPX Close'].shift(-rolling_window)
        temp_df['vix_shift'] = temp_df['VIX Close'].shift(-rolling_window)
        del temp_df['SPX Close'], temp_df['Daily VIX Close']
        temp_df.columns = ['spx','skew','vix','VIX Close','spx_shift','vix_shift']
    else:
    # Here the function will be preparing to perform the usual
    # close to close calculations
        temp_df = df[['SPX Close','skew','Daily VIX Close','VIX Close']]
        temp_df.columns = ['spx','skew','vix','VIX Close']
        temp_df['spx_shift'] = temp_df['spx'].shift(-rolling_window)
        temp_df['vix_shift'] = temp_df['VIX Close'].shift(-rolling_window)
    
    # Taking daily vix of the day and scaling to the time-span
    # specified in rolling_window, e.g., for a DTE of 5 days,
    # the daily vix will be scaled by sqrt(5)
    temp_df['period_vix'] = temp_df['vix']*np.sqrt(rolling_window)
    
    # Here, the Skew Normal Distribution is invoked to calculate the
    # worst potential 1% return assuming log returns follow a Skew
    # Normal Distribution where the SKEW index approximates the 
    # "shape" and the VIX index approximates the "scaling parameter"
    # Mean is assumed to be 0, however, further testing may be needed
    # To determine if a rolling mean-return is necessary
    
    # Adjusted so that function can check OTM Call VaR given a certain
    # probability level. Call VaR is assuming a normal distribution to
    # be conservative while Put VaR is assuming a skew normal distribution
    # to be conservative.
    if option == 'C':
        var_pct = 1 - var_pct
        temp_df['var_pct'] = norm.ppf(var_pct, 0, temp_df['period_vix'])
    else:
        temp_df['var_pct'] = skn.ppf(var_pct, temp_df['skew'], 0, temp_df['period_vix'])
    
    # Using the potential 1% return, the corresponding SPX level is
    # calculated to provide a strike suggestion for the SPX put
    temp_df['var_spx_lvl'] = temp_df['spx']*np.exp(temp_df['var_pct']) #(1 + temp_df['var_pct'])
    
    # Calculating what the percentage difference is between the actual realized
    # SPX index versus it's approximated 1% worst case return assuming an SKN
    # This column is only useful after filtering on breaches
    temp_df['actual_to_var_diff'] = temp_df['spx_shift']/temp_df['var_spx_lvl'] - 1
    
    # Calculating the actual SPX return for the given rolling_window
    temp_df['actual_spx_return'] = temp_df['spx_shift']/temp_df['spx'] - 1
    
    if option == 'C':
        plot_df = temp_df[temp_df['var_spx_lvl'] < temp_df['spx_shift']]
    else:
        plot_df = temp_df[temp_df['var_spx_lvl'] > temp_df['spx_shift']]
    
    fig, axes = plt.subplots(nrows = 2, ncols = 2, figsize = (15,15))
    plot_df[['var_pct','actual_spx_return']].plot(ax = axes[0,0])
    plot_df['actual_spx_return'].plot(ax = axes[1,0])
    plot_df['actual_to_var_diff'].hist(ax = axes[0,1])
    plot_df['VIX Close'].hist(ax = axes[1,1])
    axes[0,0].set_title('Implied VaR Returns that Breached')
    axes[1,0].set_title('Actual SPX Returns for Breach')
    axes[0,1].set_title('Distribution of Breach Percentage')
    axes[1,1].set_title('Distribution of VIX Close on Trade Day')
    fig.suptitle("SPX VaR at p: " + str(var_pct), fontsize=14)
    
    historical_prob_of_breach = 100*len(plot_df)/float(len(temp_df.dropna()))
    print("--------------------------------------------------------------------")
    print("")
    print("The historical probability of breaching is " + str(round(historical_prob_of_breach,2)) + "%")
    print("With the total occurences being " + str(len(plot_df)) + " times")
    
    if option == 'C':
        plot_df = pd.DataFrame.sort_values(plot_df,by = 'actual_to_var_diff', ascending = False)
    else:
        plot_df = pd.DataFrame.sort_values(plot_df,by = 'actual_to_var_diff')
    print("With the worst 5 cases as follows:")
    print(plot_df.head())
    print("")
    print("--------------------------------------------------------------------")
    print("")
    print("The latest SPX level and suggested strike is:")
    print(temp_df[['spx','VIX Close','skew','var_spx_lvl']].tail(3))
    
    return temp_df[['spx','spx_shift','var_pct',
                    'var_spx_lvl','actual_to_var_diff',
                    'VIX Close','vix_shift']]

# Function for simple one time calculation of a suggested
# SPX Put strike level provided we enter a DTE (rolling_window)
# VaR percent level, the current VIX index as is, the current SKEW
# Index as is and the current SPX index
def spx_implied_var_single(rolling_window, var_pct, vix, skew, spx, option = 'P'):
    alpha = -(skew - 100)/10
    period_vix = (np.sqrt(((vix*vix)/365)*1.5)/100)*np.sqrt(rolling_window)
    if option == 'C':
        var_pct = 1 - var_pct
        pct_var = norm.ppf(var_pct, 0, period_vix)
    else:
        pct_var = skn.ppf(var_pct, alpha, 0, period_vix)
    spx_k_suggestion = spx*np.exp(pct_var)#(1 + pct_var)
    print('VaR return percent for SPX is: ' + str(round(pct_var*100,2)))
    print('Suggested SPX strike: ' + str(np.floor(spx_k_suggestion)))
    
    return spx_k_suggestion

#%% Put Delta Check
import statsmodels.api as sm

spot = 2693.25
strike = 2650
delta = -0.163
gamma = 0.005
vega = 0.505
theta = -2.834
dte = 2
vixlevel = 19.35
premium = 5.50

def iv_predict(vixlevel, dte, spxstd):
    # Creating training set to estimate IV Change
    iv = df['VIX Close']/np.sqrt(252/dte)/100
    iv.name = 'IV DTE'
    vix_ret = df['VIX Close'].pct_change(dte)
    vix_ret.name = 'VIX Return'
    spx_ret = df['SPX Close'].pct_change(dte)
    spx_ret.name = 'SPX Return'
    trainingset = pd.concat([iv,vix_ret,spx_ret],axis = 1).dropna()
    trainingset['SPX Std'] = trainingset['SPX Return']/trainingset['IV DTE']
    
    linreg = sm.OLS(trainingset['VIX Return'], trainingset['SPX Std']).fit()
    iv_change = linreg.predict(spxstd)[0]
    return ((1-iv_change)*vixlevel - vixlevel)/100

def put_delta_to_strike(spot,strike,delta,gamma,vega,theta,dte,vixlevel):
    spxstd = (strike/spot-1)/(vixlevel/(100*np.sqrt(252/dte)))
    iv_change = iv_predict(vixlevel, dte, abs(spxstd))
    spxchange = strike - spot
    putprice = delta*spxchange + 0.5*gamma*(spxchange**2) + vega*iv_change + theta*dte
    return putprice

def put_delta(spot,spxstd,delta,gamma,vega,theta,dte,vixlevel):
    spxchange = -spot*(spxstd*(vixlevel/(100*np.sqrt(252/dte))))
    iv_change = iv_predict(vixlevel, dte, abs(spxstd))
    putprice = delta*spxchange + 0.5*gamma*(spxchange**2) + vega*iv_change + theta*dte
    return putprice

put_risk = pd.DataFrame({'std': np.arange(1,4,0.5),
                         'put delta': [put_delta(spot,spxstd,delta,
                                                 gamma,vega,theta,
                                                 dte,vixlevel) for spxstd in np.arange(1,4,0.5)]})
put_risk['loss'] = put_risk['put delta']/premium*100
print(put_risk)
print('\n')
print('Put Delta for hitting strike:')
print(put_delta_to_strike(spot,strike,delta,gamma,vega,theta,dte,vixlevel))

# Change in Put Price ~ Delta*($ Change in Stock) 
#                       + 0.5*Gamma*($ Change in Stock)^2 
#                       + Vega*(Change in Annualized Vol)
#                       + Theta*DTE

#%%

from yahoo_finance import Share
yahoo = Share('YHOO')

'''
Functions to write

Given SPX return, DTE, and VIX level, how often has this occurred and when

For DTE > 1, given Strike return, what is average daily return to hit strike
and how often it's occurred. When was the latest occurrence. I.e., consecutive
down days.

Given, VIX, VVIX, SKEW, and SPX, what is the implied VaR drop for SPX

Build an RUT version of all codes

'''

#%% Creating a dataframe of worst returns

vixlvls = list(np.arange(11,41))
dtes = list(np.arange(1,91))
returndict = {}

for dte in dtes:
    dte_returns = []
    for vx in vixlvls:
        dte_returns += [worst_return(vx, dte, display = False)['ret'][0]]
    returndict[dte] = dte_returns

#%%
returnsdf = pd.DataFrame(returndict, index = vixlvls)
returnsdf.to_csv('returnmatrix.csv')
