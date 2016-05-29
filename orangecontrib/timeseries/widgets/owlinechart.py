from numbers import Number
from collections import OrderedDict
from os.path import join, dirname

import numpy as np

from Orange.data import TimeVariable
from Orange.widgets import widget, gui, settings
from Orange.widgets.highcharts import Highchart

from PyQt4.QtGui import QTreeWidget, QTreeWidgetItem, QFont, QSizePolicy, \
    QWidget, QPushButton, QIcon, QTreeView, QVBoxLayout
from PyQt4.QtCore import Qt, QSize, pyqtSignal

from orangecontrib.timeseries import Timeseries


class PlotConfigWidget(QWidget):
    sigClosed = pyqtSignal(str, QWidget)
    sigLogarithmic = pyqtSignal(str, bool)
    sigType = pyqtSignal(str, str)
    sigSelection = pyqtSignal(str, list)

    is_logarithmic = False
    plot_type = 'line'

    def __init__(self, owwidget, ax):
        super().__init__(owwidget)
        self.ax = ax
        self.tree = tree = QTreeView(self,
                                     alternatingRowColors=True,
                                     selectionMode=QTreeWidget.ExtendedSelection,
                                     uniformRowHeights=True,
                                     headerHidden=True,
                                     indentation=10,
                                     size=QSize(100, 100),
                                     sizePolicy=QSizePolicy(QSizePolicy.Fixed,
                                                            QSizePolicy.Expanding))
        tree.setModel(owwidget.tree.model())
        tree.header().setStretchLastSection(True)
        tree.expandAll()
        selection = tree.selectionModel()
        selection.selectionChanged.connect(self.selection_changed)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.setLayout(box)

        hbox = gui.hBox(self)
        gui.comboBox(hbox, self, 'plot_type',
                     label='Type:',
                     orientation='horizontal',
                     items=('line', 'step line', 'column', 'area', 'spline'),
                     sendSelectedValue=True,
                     callback=lambda: self.sigType.emit(ax, self.plot_type))
        gui.rubber(hbox)
        self.button_close = button = QPushButton('×', hbox,
                                                 visible=False,
                                                 minimumSize=QSize(20, 20),
                                                 maximumSize=QSize(20, 20),
                                                 styleSheet='''
                                                     QPushButton {
                                                         font-weight: bold;
                                                         font-size:14pt;
                                                         margin:0;
                                                         padding:0;
                                                     }''')
        button.clicked.connect(lambda: self.sigClosed.emit(ax, self))
        hbox.layout().addWidget(button)
        gui.checkBox(self, self, 'is_logarithmic', 'Logarithmic axis',
                     callback=lambda: self.sigLogarithmic.emit(ax, self.is_logarithmic))
        box.addWidget(tree)

    def enterEvent(self, event):
        self.button_close.setVisible(True)
    def leaveEvent(self, event):
        self.button_close.setVisible(False)

    def selection_changed(self, _s, _d):
        selection = []
        for mi in self.tree.selectionModel().selectedIndexes():
            if not mi.parent().isValid(): continue
            data_id = mi.parent().data(Qt.UserRole)
            attr = mi.data(Qt.UserRole)
            selection.append((data_id, attr))
        self.sigSelection.emit(self.ax, selection)


class Highstock(Highchart):

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args,
                         yAxis_lineWidth=2,
                         yAxis_labels_x=6,
                         yAxis_labels_y=-3,
                         yAxis_labels_align_x='right',
                         yAxis_title_text=None,
                         plotOptions_areasplinerange_states_hover_lineWidthPlus=0,
                         plotOptions_areasplinerange_tooltip_pointFormat='''
                            <span style="color:{point.color}">\u25CF</span>
                            {series.name}: <b>{point.low:.2f} – {point.high:.2f}</b><br/>''',
                         **kwargs)
        self.parent = parent
        self.axes = []

    def _resizeAxes(self):
        if not self.axes:
            return
        MARGIN = 2
        HEIGHT = (100 - (len(self.axes) - 1) * MARGIN) // len(self.axes)
        self.evalJS('''
            var SKIP_AXES = 2,
                HEIGHT = %(HEIGHT)f,
                MARGIN = %(MARGIN)f;
            for (var i = 0; i < chart.yAxis.length - SKIP_AXES; ++i) {
                var top = i * (HEIGHT + MARGIN);
                chart.yAxis[i + SKIP_AXES].update({
                    top: top + '%%',
                    height: HEIGHT + '%%',
                    offset: 0  // Fixes https://github.com/highcharts/highcharts/issues/5199
                }, false);
            }
            chart.reflow();
            chart.redraw(false);
        ''' % locals())

    def addAxis(self):
        from random import random
        ax = 'ax_' + str(random())[2:]
        self.axes.append(ax)
        self.evalJS('''
            chart.addAxis({
                id: '%(ax)s',
            }, false, false, false);
        ''' % locals())
        self._resizeAxes()
        # TODO: multiple series on the bottom navigator, http://jsfiddle.net/highcharts/SD4XN/
        return ax

    def removeAxis(self, ax):
        self.axes.remove(ax)
        self.evalJS('''
            chart.get('%(ax)s').remove();
        ''' % dict(ax=ax))
        self._resizeAxes()

    def setSeries(self, ax, series):
        """TODO: Clean this shit up"""
        newseries = []
        names = []
        deltas = []
        forecasts = []
        forecasts_ci = []
        ci_percents = []
        delta = None

        for data_id, _ in series:
            data = self.parent.datas[data_id]
            if isinstance(data.time_variable, TimeVariable):
                delta = data.time_delta
            break

        for data_id, attr in series:
            data = self.parent.datas[data_id]
            newseries.append(np.ravel(data[:, attr]))
            names.append(attr.name)
            deltas.append(None)
            forecasts.append(None)
            forecasts_ci.append(None)
            ci_percents.append(None)

            for forecast in self.parent.forecasts.values():
                fc_attr = attr.name + ' (forecast)'
                if fc_attr in forecast.domain:
                    fc_attr = forecast.domain[fc_attr]
                    # Forecast extends from last known value
                    forecasts[-1] = np.concatenate((newseries[-1][-1:],
                                                    np.ravel(forecast[:, fc_attr])))
                    # ci_attrs = forecast.attributes.get('ci_attrs', {})
                    # ci_low, ci_high = ci_attrs.get(fc_attr, (None, None))
                    ci_low, ci_high = getattr(fc_attr, 'ci_attrs', (None, None))
                    if ci_low in forecast.domain and ci_high in forecast.domain:
                        ci_percents[-1] = ci_low.ci_percent
                        forecasts_ci[-1] = np.row_stack((
                            [newseries[-1][-1]] * 2,  # last known value
                            np.column_stack((
                                np.ravel(forecast[:, ci_low]),
                                np.ravel(forecast[:, ci_high])))))
                    break

            if isinstance(data.time_variable, TimeVariable):
                delta = data.time_delta
                tvals = data.time_values

                if isinstance(delta, Number):
                    deltas[-1] = (tvals[0] * 1000, tvals[-1] * 1000, delta, None)
                elif delta:
                    deltas[-1] = (tvals[0] * 1000, tvals[-1] * 1000) + delta
                else:
                    newseries[-1] = np.column_stack((tvals * 1000,
                                                     newseries[-1]))
                    if forecasts[-1] is not None:
                        if forecast.time_variable:  # Use forecast time if available
                            fc_tvals = np.concatenate((tvals[-1:],
                                                       forecast.time_values * 1000))
                            forecasts[-1] = np.column_stack((
                                fc_tvals, forecasts[-1]))
                            forecasts_ci[-1] = np.column_stack((
                                fc_tvals, forecasts_ci[-1]))
                        else:  # Extrapolate from the final time of data
                            fc_tvals = np.linspace(
                                1000 * tvals[-1],
                                1000 * (tvals[-1] + (len(forecasts[-1]) - 1) * np.diff(tvals[-2:])[0]),
                                len(forecasts[-1]))
                            forecasts[-1] = np.column_stack((
                                fc_tvals, forecasts[-1]))
                            forecasts_ci[-1] = np.column_stack((
                                fc_tvals, forecasts_ci[-1]))

        self.exposeObject('series_' + ax, {'data': newseries,
                                           'forecasts': forecasts,
                                           'forecasts_ci': forecasts_ci,
                                           'ci_percents': ci_percents,
                                           'names': names,
                                           'deltas': deltas})
        self.evalJS('''
            var ax = chart.get('%(ax)s');
            chart.series
            .filter(function(s) { return s.yAxis == ax })
            .map(function(s) { s.remove(false); });

            var data = series_%(ax)s.data,
                names = series_%(ax)s.names,
                deltas = series_%(ax)s.deltas,
                forecasts = series_%(ax)s.forecasts,
                ci_percents = series_%(ax)s.ci_percents,
                forecasts_ci = series_%(ax)s.forecasts_ci;

            for (var i=0; i < data.length; ++i) {
                var opts = {
                    data: data[i],
                    name: names[i],
                    yAxis: '%(ax)s'
                };

                if (deltas[i]) {
                    opts.pointStart = deltas[i][0];
                    // skip 1: pointEnd (forecast start)
                    opts.pointInterval = deltas[i][2];
                    if (deltas[i][3])
                        opts.pointIntervalUnit = deltas[i][3];
                }

                var added_series = chart.addSeries(opts, false, false);

                if (forecasts[i]) {
                    var opts = {
                        linkedTo: ':previous',
                        name: names[i] + ' (forecast)',
                        yAxis: '%(ax)s',
                        data: forecasts[i],
                        dashStyle: 'ShortDash',
                        color: added_series.color,
                        fillOpacity: .3,
                    };
                    if (deltas[i]) {
                        opts.pointStart = deltas[i][1];
                        opts.pointInterval = deltas[i][2];
                        if (deltas[i][3])
                            opts.pointIntervalUnit = deltas[i][3];
                    }
                    chart.addSeries(opts, false, false)
                }
                if (forecasts_ci[i]) {
                    var opts = {
                        type: 'areasplinerange',
                        linkedTo: ':previous',
                        name: names[i] + ' (forecast; ' + ci_percents[i] + '%% CI)',
                        yAxis: '%(ax)s',
                        data: forecasts_ci[i],
                        color: added_series.color,
                        fillOpacity: 0.2,
                        lineWidth: 0,
                    };
                    if (deltas[i]) {
                        opts.pointStart = deltas[i][1];
                        opts.pointInterval = deltas[i][2];
                        if (deltas[i][3])
                            opts.pointIntervalUnit = deltas[i][3];
                    }
                    chart.addSeries(opts, false, false)
                }
            }
            chart.redraw(false);
        ''' % dict(ax=ax))

    def setLogarithmic(self, ax, is_logarithmic):
        self.evalJS('''
            chart.get('%(ax)s').update({ type: '%(type)s' });
        ''' % dict(ax=ax, type='logarithmic' if is_logarithmic else 'linear'))

    def setType(self, ax, type):
        step, type = ('true', 'line') if type == 'step line' else ('false', type)
        self.evalJS('''
            var ax = chart.get('%(ax)s');
            chart.series
            .filter(function(s) { return s.yAxis == ax; })
            .map(function(s) {
                s.update({
                    type: '%(type)s',
                    step: %(step)s
                }, false);
            });
            chart.redraw(false);
        ''' % locals())

    def enable_rangeSelector(self, enable):
        display = 'initial' if enable else 'none'
        self.evalJS(
            '$(".highcharts-range-selector-buttons, '
            '   .highcharts-input-group").css({display: "%s"})' % display)
        # Reset the range selector to full view
        if not enable:
            self.evalJS('$(chart.rangeSelector.buttons[5]).click();')


class OWLineChart(widget.OWWidget):
    name = 'Line Chart'
    description = "Visualize time series' sequence and progression."
    icon = 'icons/LineChart.svg'
    priority = 90

    inputs = [("Time series", Timeseries, 'set_data', widget.Multiple),
              ('Forecast', Timeseries, 'set_forecast', widget.Multiple)]

    attrs = settings.Setting({})  # Maps data.name -> [attrs]

    def __init__(self):
        self.plots = []
        self.configs = []
        self.datas = OrderedDict()
        self.forecasts = OrderedDict()
        self.tree = QTreeWidget(columnCount=1,
                                alternatingRowColors=True,
                                selectionMode=QTreeWidget.ExtendedSelection,
                                uniformRowHeights=True,
                                headerHidden=True)
        icon = QIcon(join(dirname(__file__), 'icons', 'LineChart-plus.png'))
        self.add_button = button = QPushButton(icon, ' &Add plot', self)
        button.clicked.connect(self.add_plot)
        self.controlArea.layout().addWidget(button)
        self.configsArea = gui.vBox(self.controlArea)
        self.controlArea.layout().addStretch(1)
        self.highstock = highstock = Highstock(self, self, highchart='StockChart', debug=True)
        self.mainArea.layout().addWidget(highstock)
        highstock.chart()

    def add_plot(self):
        ax = self.highstock.addAxis()
        config = PlotConfigWidget(self, ax)
        # Connect the signals
        config.sigSelection.connect(self.highstock.setSeries)
        config.sigLogarithmic.connect(self.highstock.setLogarithmic)
        config.sigType.connect(self.highstock.setType)
        config.sigClosed.connect(self.highstock.removeAxis)
        config.sigClosed.connect(lambda ax, widget: widget.setParent(None))
        config.sigClosed.connect(lambda ax, widget:
                                 self.add_button.setDisabled(False))
        self.configs.append(config)
        self.add_button.setDisabled(len(self.configs) >= 5)
        self.configsArea.layout().addWidget(config)

    def set_data(self, data, id):

        def tree_remove(id):
            row = list(self.datas.keys()).index(id)
            self.tree.takeTopLevelItem(row)

        def tree_add(id, data):
            top = QTreeWidgetItem(
                [data.name or '<{}>'.format(data.__class__.__name__)])
            top.setData(0, Qt.UserRole, id)
            top.setFont(0, QFont('', -1, QFont.Bold))
            self.tree.addTopLevelItem(top)
            for attr in data.domain.variables:
                if not attr.is_continuous or attr == data.time_variable:
                    continue
                item = QTreeWidgetItem(top, [attr.name])
                item.setData(0, Qt.UserRole, attr)
            self.tree.expandItem(top)

        # TODO: only single data?
        if data is None:
            try:
                tree_remove(id)
                self.datas.pop(id)
            except (ValueError, KeyError):
                pass
        else:
            self.highstock.enable_rangeSelector(
                isinstance(data.time_variable, TimeVariable))
            if id in self.datas:
                tree_remove(id)
            self.datas[id] = data
            tree_add(id, data)

    def set_forecast(self, forecast, id):
        if forecast is not None:
            self.forecasts[id] = forecast
        else:
            self.forecasts.pop(id, None)
        # TODO: update currently shown plots


if __name__ == "__main__":
    from PyQt4.QtGui import QApplication
    from orangecontrib.timeseries import ARIMA, VAR

    a = QApplication([])
    ow = OWLineChart()

    msft = Timeseries('yahoo_MSFT')
    ow.set_data(msft, 0),
    ow.set_data(Timeseries('UCI-SML2010-1'), 1)

    msft = msft.interp()
    model = ARIMA((3, 1, 1)).fit(msft)
    ow.set_forecast(model.predict(10, as_table=True), 0)
    model = VAR(4).fit(msft)
    ow.set_forecast(model.predict(10, as_table=True), 1)

    ow.show()
    a.exec()
